# 3. The first run

Starting cleanly and actually receiving are two different things. This page
shows you how to tell the difference, and what to change if it is not working.

---

## 3.1 Watch it start

```bash
docker compose up -d
docker compose logs -f trunk-recorder
```

The first minute is mostly setup. What you are looking for:

**The Airspy was found and tuned:**

```
SDR Device 0: AirSpy ... 
Tuning to 852.500056 MHz
```

That number is your `center` (852.503125 MHz) plus your `error` (-3069 Hz). If
you see it, the radio is talking to the computer.

**The control channel started:**

```
[gjdtrs]	Started with Control Channel: 852.562500
```

**Then, a couple of minutes later**, a status block:

```
Control Channel Decode Rates: 
[gjdtrs]	852.562500	38 msg/sec
```

**That decode rate is the single most important number in this whole setup.**

| Rate | What it means |
| --- | --- |
| **35–45 msg/sec** | Healthy. You are receiving properly. |
| **10–35 msg/sec** | Marginal. Calls will be missed and audio will be rough. Improve the antenna or adjust the tuning below. |
| **Under 10** | Not working. You will also see red error lines. |
| **0** | Receiving nothing at all. |

If it is below 10, Trunk Recorder logs it as an error every couple of minutes:

```
[gjdtrs]	freq: 852.562500	Control Channel Message Decode Rate: 3/sec, count: 62
```

Press `Ctrl+C` to stop watching the log.

---

## 3.2 If the decode rate is low

Work through these in order. Change **one thing at a time** and give it a few
minutes to show a new decode rate.

Edit the config with:

```bash
nano config/trunk-recorder/config.json
```

and apply changes with:

```bash
docker compose restart trunk-recorder
```

### a) The antenna

Nine times out of ten it is the antenna. Re-read
[docs/01-hardware.md](01-hardware.md). Getting it higher and outside beats every
software change on this page combined.

### b) Gain

This is how much the receiver amplifies. Too little and the signal is buried in
noise; too much and the receiver overloads, which also destroys the signal.

Find this section:

```json
"gainSettings": {
  "LNA": 12,
  "MIX": 8,
  "IF": 10
}
```

Try raising `LNA` to `14`, then `16`. If the decode rate gets *worse* as you
raise it, you are overloading — go the other way instead, down to `8`.

The valid range for each is roughly 0–15. Change one at a time.

> If the log says something like `Requested LNA Gain of 12 not supported`, the
> stage names differ on your build. Look further up the startup log for the list
> of gain stage names it does recognise and use those instead. As a fallback,
> delete the whole `gainSettings` block and use a single `"gain": 18,` line.

### c) Frequency error

Radios drift. If the receiver is tuned slightly off, the decode rate suffers.

```json
"error": -3069,
```

This is a correction in **hertz**. The starting value of −3069 Hz comes from
someone else who receives this same site and needed −3.6 ppm of correction.

Two things can cause that offset — the receiver's crystal being slightly off, or
the transmitter site itself being slightly off. If it is the site, this value
carries over to your Airspy. If it is their crystal, it does not. You will find
out by trying.

You do not have to guess, because `"autoTune": true` is already set. Trunk
Recorder measures the real error while it runs and corrects it. If the starting
point is badly wrong, it says so:

```
Source 0 - AutoTune offset: 4200 Hz exceeds 3.5 PPM (based on center freq
852.503125 MHz). Verify initial offset used in config file.
```

If you see that, **add the reported offset to your `error` value** and restart.
In the example above: −3069 + 4200 = `1131`, so you would set `"error": 1131`.

If you get nothing at all, try `"error": 3069` (the same number, positive) —
that rules out the sign being backwards.

### d) Interference

If you live near a broadcast or cellular tower, a strong out-of-band signal can
swamp the receiver even though it is nowhere near 852 MHz. The symptom is a
decode rate that stays poor no matter what you do with gain. The fix is a
band-pass filter — see [docs/01-hardware.md](01-hardware.md).

---

## 3.3 Confirm calls are being recorded

Once the decode rate looks healthy, wait for some radio traffic, then:

```bash
ls -R media/
```

You should see files grouped by date, in sets of three:

```
media/gjdtrs/2026/7/29/
    8441-1785333627_853962500-call_1.json
    8441-1785333627_853962500-call_1.m4a
    8441-1785333627_853962500-call_1.wav
```

`8441` is the talkgroup, so that example is a Grand Junction Fire dispatch call.

Nothing appearing? Check the log for `Call Started` lines. If the control
channel decodes fine but no calls record, the traffic may all be encrypted, or
it may simply be quiet — try again during business hours.

---

## 3.4 Confirm transcription is working

```bash
docker compose logs -f transcriber
```

The very first start downloads the speech model — around 3 GB, so give it a few
minutes. Then:

```
loading Whisper model large-v3 (device=cuda, compute_type=int8)
model ready in 8.4s
watching /media
```

And as calls come in:

```
[GJ Fire Disp] 6.4s in 2.1s rtf 0.33  Engine 1, Medic 3, respond to a structure fire...
```

`rtf` is "real-time factor" — seconds of computer time per second of audio.
**Below 1.0 means it can keep up.** If it creeps above 1.0 you will eventually
see:

```
73 calls waiting - transcription is falling behind.
```

That is what [docs/05-tuning-whisper.md](05-tuning-whisper.md) is for.

**Common first-run errors:**

| Message | Cause |
| --- | --- |
| `could not find any NVIDIA driver` | The GPU is not reaching the container — redo [step 2.4](02-server-setup.md#24-let-docker-use-the-graphics-card) |
| `Library libcudnn_ops.so is not found` | Rare image mismatch — `docker compose build --no-cache transcriber` |
| `CUDA failed with error out of memory` | Something else is using the card, or the model is too big — try `WHISPER_MODEL=distil-large-v3` |

---

## 3.5 Set up the listening page (Rdio Scanner)

Recording works without this. This is only for the scanner-style page on
port 3000.

1. Open `http://<your-server>:3000/admin` in a browser.

2. Log in with the password **`rdio-scanner`**. It will immediately ask you to
   change it. Do that and remember the new one.

3. Go to **Systems** and click the **+** to add one. Set:

   - **System ID:** `1`
   - **Label:** `gjdtrs`

   Save it.

4. Go to **API keys** and add a key. Give it any name. It generates a long
   random string — **copy it**.

5. Back on the server:

   ```bash
   nano config/trunk-recorder/config.json
   ```

   Near the bottom, replace `CHANGE_ME` with the key you copied:

   ```json
   "apiKey": "paste-the-long-string-here"
   ```

   Save (`Ctrl+O`, `Enter`, `Ctrl+X`), then:

   ```bash
   docker compose restart trunk-recorder
   ```

6. Open `http://<your-server>:3000`. New calls should start appearing.

If they do not, check `docker compose logs trunk-recorder` for
`Rdio Scanner Upload Error` — that means the API key or system ID does not match
what you set in the dashboard.

> Talkgroups appear in Rdio Scanner automatically as they are heard, but you can
> tidy up their names and grouping in the admin dashboard under **Talkgroups**.

---

## 3.6 Check the transcript page

Open `http://<your-server>:8080`.

You should see calls appearing with their transcripts, newest at the top, each
with a play button. The page refreshes itself every few seconds — except while
you have audio playing, or while a search filter is active.

---

**Next:** [4. Talkgroup names](04-talkgroups.md)
