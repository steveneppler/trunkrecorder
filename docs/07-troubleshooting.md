# 7. Troubleshooting

Organised by symptom. Find the thing that is happening to you.

**First, always:**

```bash
docker compose ps          # is everything actually running?
docker compose logs --tail 50
```

If a container shows `Restarting` or `Exited`, look at its log specifically:

```bash
docker compose logs --tail 100 trunk-recorder
docker compose logs --tail 100 transcriber
```

---

## Nothing is being recorded

### The log says `No supported devices found` or similar

The Airspy is not reachable.

```bash
./scripts/check-sdr.sh
```

- Not listed in `lsusb`? Unplug and replug it. Use a USB 3 port directly on the
  computer, not a hub.
- Listed, but "cannot open device"? The udev rule is missing — see
  [section 2.5](02-server-setup.md#25-let-the-airspy-be-used-without-root),
  then **unplug and replug**.
- Another program may have it. `sdrtrunk`, `gqrx`, and SDR# all hold the device
  exclusively. Close them.
- If you have **more than one SDR plugged in**, `"device": "airspy"` is
  ambiguous. Get the serial with `airspy_info` and name it explicitly in
  `config/trunk-recorder/config.json`:

  ```json
  "device": "airspy=644064dc2b53a1c3",
  ```

### The decode rate is 0 or very low

This is the big one. It has its own section:
**[3.2 If the decode rate is low](03-first-run.md#32-if-the-decode-rate-is-low)**.

Short version, in order of likelihood: the antenna, the gain, the frequency
error, interference.

### The decode rate is fine but no calls appear

- It may genuinely be quiet. Try during business hours on a weekday.
- The traffic may be encrypted. Encrypted calls are logged and skipped —
  look for `encrypted` in the transcriber log or on the web page.
- Check `digitalRecorders` in `config.json` is at least 4. If the log says
  `No Digital Recorders Available`, raise it.

### The control channel keeps changing or dropping

The site may have moved to a different control channel. Trunk Recorder can
switch automatically if you give it alternatives. Add them to
`config/trunk-recorder/config.json`:

```json
"control_channels": [852562500, 851437500, 853962500],
```

Frequencies for the site are on
[RadioReference](https://www.radioreference.com/db/sid/329) — see
[docs/04-talkgroups.md](04-talkgroups.md).

---

## It was recording, then quietly stopped

This one is worth understanding, because it does not look like a failure.

If the Airspy drops off USB for even a fraction of a second, Trunk Recorder
does not crash and does not exit. It tears down its GNU Radio worker threads
and keeps running — 0% CPU, no calls, no errors, container still "Up". It
happened here on 2026-09-13 and went unnoticed for fifteen hours:

```
usb 1-5: USB disconnect, device number 2
usb 1-5: new high-speed USB device number 3 using xhci_hcd
```

`restart: unless-stopped` cannot catch this. A restart policy reacts to a
process **exiting**, and this process never exits.

### What now watches for it

Two pieces, both in `docker-compose.yml`:

- a `healthcheck:` on `trunk-recorder` that runs
  `scripts/trunk-recorder-healthcheck.sh` once a minute. It counts Trunk
  Recorder's threads and fails when they collapse. A working recorder has one
  thread per GNU Radio block — 209 on this machine — and a recorder that has
  lost its SDR has almost none. It deliberately does **not** check "has a call
  been recorded recently", because a quiet night is not a fault.
- the `autoheal` service, which restarts any container whose healthcheck has
  gone unhealthy. Docker will not do this on its own: a healthcheck only sets a
  status label, and nothing acts on it.

Expect recovery to take two to four minutes — three consecutive failed probes
a minute apart, then the restart.

### Checking on it

```bash
docker compose ps                    # look for (healthy) / (unhealthy)
docker inspect --format '{{json .State.Health}}' trunk-recorder | jq
```

The last few probe results are kept there, so you can see what it measured.
Your own healthy thread count:

```bash
docker exec trunk-recorder sh -c 'ls /proc/1/task | wc -l'
```

Restarts are logged by autoheal:

```bash
docker compose logs autoheal
```

### If it restarts when it should not

The threshold is 40% of the highest thread count seen since the container
started, so it adapts to your `digitalRecorders` setting rather than assuming
this machine's numbers. If you still need to loosen it, set either of these in
`.env`:

```bash
TR_HEALTH_MIN_RATIO=25     # percent of peak; default 40
TR_HEALTH_MIN_THREADS=12   # hard floor used before a peak is established; default 20
```

Then `docker compose up -d trunk-recorder`. The script explains both in its
header comment.

### Recovering without the Docker socket

`autoheal` mounts `/var/run/docker.sock`. That socket is the full Docker API,
which is equivalent to root on the host — anything that can reach it can start
a privileged container that mounts your entire filesystem. Mounting it `:ro`
is commonly suggested and does not help; the flag applies to the bind mount,
not to what the API will do.

If you would rather not accept that, delete the `autoheal` service and run the
same check from the host, where no container needs the socket at all:

```bash
sudo tee /usr/local/bin/trunk-recorder-watchdog >/dev/null <<'EOF'
#!/bin/sh
[ "$(docker inspect -f '{{.State.Health.Status}}' trunk-recorder 2>/dev/null)" = unhealthy ] \
  && docker restart trunk-recorder
exit 0
EOF
sudo chmod +x /usr/local/bin/trunk-recorder-watchdog

sudo tee /etc/systemd/system/trunk-recorder-watchdog.service >/dev/null <<'EOF'
[Unit]
Description=Restart trunk-recorder when its healthcheck reports unhealthy
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/trunk-recorder-watchdog
EOF

sudo tee /etc/systemd/system/trunk-recorder-watchdog.timer >/dev/null <<'EOF'
[Unit]
Description=Check trunk-recorder health every minute

[Timer]
OnBootSec=5min
OnUnitActiveSec=1min
AccuracySec=10s

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now trunk-recorder-watchdog.timer
```

The `healthcheck:` block stays exactly as it is — only the thing acting on it
changes. Confirm it is ticking with
`systemctl list-timers trunk-recorder-watchdog.timer`.

### Why the Airspy dropped in the first place

Separately from recovering, it is worth fixing the cause. A disconnect and
reconnect in the same second, on a device drawing the full 500 mA a USB 2 port
allows, usually means power or the connector rather than software. Try a
different port directly on the machine, a shorter or better cable, or a
powered hub. Check whether it has happened before:

```bash
journalctl -k --since "7 days ago" | grep -i 'usb.*disconnect'
```

One isolated event in a month is bad luck. A pattern is hardware.

---

## The audio is garbled or robotic

This is reception quality, not a setting, and no amount of configuration fixes
it.

1. **The antenna.** See [docs/01-hardware.md](01-hardware.md). Higher, outside,
   tuned for 800 MHz. This is almost always the answer.
2. **Gain.** [Section 3.2b](03-first-run.md#b-gain). Both too much and too
   little produce bad audio.
3. **Simulcast distortion.** Some sites transmit the same signal from several
   towers at once. Where those signals overlap they interfere with each other,
   and the result is garbled audio even with a strong signal. A **directional
   antenna** pointed at one tower is the fix. This is a known weak spot for
   Trunk Recorder — sdrtrunk handles simulcast better — so if you can decode a
   site cleanly in sdrtrunk but not here, this is likely why.
4. **USB problems.** Dropouts from a hub or an underpowered port cause gaps that
   sound like poor reception. Direct USB 3 port only.

---

## Transcription problems

### `could not find any NVIDIA driver`

The container cannot see the graphics card.

```bash
nvidia-smi                                                        # driver OK on the host?
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi   # visible to Docker?
```

If the first works and the second does not, redo
[section 2.4](02-server-setup.md#24-let-docker-use-the-graphics-card).

### `CUDA failed with error out of memory`

Something else is using the card (a desktop session, a game, another AI tool),
or the model is too large.

```bash
nvidia-smi          # see what is using memory
```

In `.env`, drop to a smaller model:

```bash
WHISPER_MODEL=distil-large-v3
```

Make sure `WHISPER_COMPUTE_TYPE=int8` — `float16` uses far more memory on a
GTX 10-series card. See [docs/05-tuning-whisper.md](05-tuning-whisper.md).

### `Library libcudnn_ops.so is not found`

Rebuild the container:

```bash
docker compose build --no-cache transcriber
docker compose up -d transcriber
```

### It is falling behind

```
73 calls waiting - transcription is falling behind.
```

Nothing is lost — it works through the backlog oldest-first. To catch up
permanently, see [docs/05-tuning-whisper.md](05-tuning-whisper.md).

### The text is nonsense

Play the call on the transcript page first. **If it sounds garbled to you, the
problem is reception, not transcription** — see above.

If the audio is clear but the words are wrong, that is the realistic ceiling for
radio audio. Adding local place and agency names to `INITIAL_PROMPT` helps; see
[docs/05-tuning-whisper.md](05-tuning-whisper.md#add-local-vocabulary).

### Transcripts say "Thank you." or "Subtitles by the amara.org community"

Whisper inventing text from silence. Common ones are filtered already; add new
ones to `HALLUCINATION_PHRASES` in `transcriber/transcriber.py` and rebuild.

---

## Web pages

### Neither page loads

```bash
docker compose ps
```

Check you are using the right address — run `hostname -I` on the server. And
`http://`, not `https://`.

If you set `WEB_BIND=127.0.0.1` in `.env`, the page is only reachable from the
server itself. Set it back to `0.0.0.0`.

The firewall may be blocking it:

```bash
sudo ufw allow 8080/tcp
sudo ufw allow 3000/tcp
```

### The transcript page is empty

It only shows calls that have been transcribed. Check
`docker compose logs transcriber` — if the model is still downloading on first
run, wait.

### Rdio Scanner shows no calls

The upload is not getting through. In `docker compose logs trunk-recorder`, look
for `Rdio Scanner Upload Error`.

Almost always the API key or system ID does not match the admin dashboard. Redo
[section 3.5](03-first-run.md#35-set-up-the-listening-page-rdio-scanner) and
check both:

- the system ID in the dashboard is `1`, matching `"systemId": 1`
- the API key is pasted in full, with no stray spaces or quotes

### The transcript page keeps jumping while I read

It refreshes every 5 seconds. It pauses automatically while audio is playing, or
while a search or category filter is active — use a filter, or start playing a
call, to hold it still.

---

## Disk and performance

### The disk is filling up

```bash
df -h .
du -sh media/
```

Recordings are deleted after `RETENTION_DAYS` (30 by default). Lower it in
`.env` and restart the transcriber:

```bash
RETENTION_DAYS=14
```

```bash
docker compose up -d transcriber
```

To reclaim space immediately, delete old day-folders under `media/` by hand.

Set `"compressWav": true` and consider dropping the `.wav` files once
transcribed — they are roughly ten times the size of the `.m4a`.

### The computer is sluggish

Processing 10 MHz of radio spectrum is genuinely demanding. Options:

- Reduce `digitalRecorders` in `config.json` from `6` to `4`.
- If you have an Airspy **Mini**, set `"rate": 6000000` — it is meant to run at
  6 MHz, and 10 MHz costs CPU for bandwidth you are not using.
- Use a faster/smaller Whisper model
  ([docs/05-tuning-whisper.md](05-tuning-whisper.md)).

---

## Starting over

Wipe recordings and transcripts but keep your settings:

```bash
docker compose down
rm -rf media/ data/ logs/
docker compose up -d
```

Wipe everything including the Rdio Scanner configuration:

```bash
docker compose down -v
rm -rf media/ data/ logs/ rdio-data/
docker compose up -d
```

You will need to redo the Rdio Scanner admin setup after the second one.

---

## Getting help

Useful things to include when asking:

```bash
docker compose ps
docker compose logs --tail 100 trunk-recorder
docker compose logs --tail 50 transcriber
nvidia-smi
cat config/trunk-recorder/config.json
```

**Remove your Rdio Scanner API key and any Discord webhook URLs before posting
any of this publicly.**

Where to ask:

- [Trunk Recorder discussions](https://github.com/TrunkRecorder/trunk-recorder/discussions) — recording and SDR problems
- [RadioReference forums](https://forums.radioreference.com/) — Colorado DTRS and local coverage questions
- [Rdio Scanner discussions](https://github.com/chuot/rdio-scanner/discussions) — the listening interface
