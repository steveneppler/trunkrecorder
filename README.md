# Grand Junction Scanner Recorder

Records the public-safety radio traffic for **Grand Junction and Mesa County,
Colorado**, writes down everything that is said using AI speech recognition, and
gives you two ways to follow along:

| | |
| --- | --- |
| **Listen** | A scanner-style web page, like a real scanner radio in your browser |
| **Read** | A live, searchable feed of transcripts — search months of traffic for "structure fire" |
| **Discord** | Optionally post calls, with audio and transcript, into Discord channels |

It listens to the Colorado state radio system (**DTRS**) site that serves the
Grand Junction area — the same system used by Grand Junction Fire, Mesa County
agencies, and CDOT.

Everything runs on one computer in your house using Docker, so it is a handful
of commands to set up and a single command to start.

> **New to Linux?** That is fine — this guide assumes you have never used a
> terminal before. Follow the numbered steps in order and do not skip ahead.
> Every command is meant to be copied and pasted exactly.

---

## What you need

**A computer to run it on.** Any desktop PC running Ubuntu will do. It needs:

- an **NVIDIA graphics card** — a GTX 1070 or better is plenty
- a decent CPU (4+ cores) — processing the radio signal is the demanding part
- **100 GB or more of free disk space**
- a wired network connection, ideally

**An Airspy R2 or Airspy Mini** software-defined radio. This is the piece of
hardware that actually receives the radio signal. It plugs into USB.

**An antenna** for the 800 MHz band, mounted as high as you can get it. This
matters more than anything else on this list — see [docs/01-hardware.md](docs/01-hardware.md).

---

## Setting it up

Work through these in order. Each links to a page with the actual detail.

### 1. Get the hardware right — [docs/01-hardware.md](docs/01-hardware.md)

Which antenna, where to put it, what cable to use, and how to plug the Airspy in.
A bad antenna is the number one reason these setups do not work, so it is worth
reading this before you buy anything.

### 2. Prepare the computer — [docs/02-server-setup.md](docs/02-server-setup.md)

Installing Ubuntu, Docker, the NVIDIA graphics driver, and the piece that lets
Docker use the graphics card. This is the longest step and the one where people
most often get stuck, so it is written out click by click.

### 3. Download this project

```bash
sudo apt install -y git
git clone https://github.com/steveneppler/trunkrecorder.git
cd trunkrecorder
```

### 4. Create your settings file

```bash
cp .env.example .env
nano .env
```

`nano` is a simple text editor. Use the arrow keys to move around, type your
changes, then press `Ctrl+O` and `Enter` to save, and `Ctrl+X` to quit.

You can leave everything at its defaults to start. Discord is off by default.

### 5. Check everything is ready

```bash
./scripts/check-sdr.sh
```

This looks for your Airspy, checks Docker is working, and confirms Docker can
see the graphics card. Fix anything it complains about before continuing — it
tells you which page explains each problem.

### 6. Start it

```bash
docker compose up -d
```

The first time you run this it will take a while: it downloads several GB of
software, and the transcriber downloads the speech recognition model. Watch what
it is doing with:

```bash
docker compose logs -f
```

Press `Ctrl+C` to stop watching (this does not stop the system).

### 7. Confirm it is actually receiving — [docs/03-first-run.md](docs/03-first-run.md)

**Do not skip this.** It shows you how to tell whether the radio is tuned
correctly, and how to adjust it if not. A stack that starts cleanly but receives
nothing looks identical to one that is working until you know what to look for.

### 8. Open the web pages

- **Transcripts:** `http://<your-server>:8080`
- **Listen:** `http://<your-server>:3000`

Replace `<your-server>` with the computer's address on your network. If you do
not know it, run `hostname -I` on the server.

Rdio Scanner (the listening page) needs a one-time setup to accept recordings —
[docs/03-first-run.md](docs/03-first-run.md) walks through it.

### 9. Fill in the talkgroup names — [docs/04-talkgroups.md](docs/04-talkgroups.md)

Out of the box, only four talkgroups have names; everything else shows up as a
number. This page explains how to get the full list for Mesa County.

### 10. Optional: post to Discord — [docs/06-discord.md](docs/06-discord.md)

Nothing is posted until you set this up deliberately.

---

## Everyday commands

Run these from inside the `trunkrecorder` folder.

```bash
docker compose up -d          # start everything (also applies config changes)
docker compose down           # stop everything
docker compose restart        # restart everything
docker compose ps             # what is running right now

docker compose logs -f                  # watch everything
docker compose logs -f trunk-recorder   # just the radio receiver
docker compose logs -f transcriber      # just the transcription

df -h .                       # how much disk space is left
```

After editing `.env` or anything in `config/`, apply it with:

```bash
docker compose up -d
```

---

## When something is wrong

Start with **[docs/07-troubleshooting.md](docs/07-troubleshooting.md)**. It is
organised by symptom — "no calls are being recorded", "the audio is garbled",
"transcripts are gibberish", "the disk filled up".

The other reference pages:

- [docs/05-tuning-whisper.md](docs/05-tuning-whisper.md) — making transcription faster or more accurate

---

## A few honest warnings

**Do not expose this to the internet.** Neither web page has a password. Anyone
who can reach them can listen to everything. Keep them on your home network. Do
not forward ports 8080 or 3000 through your router. If you want access from
outside, use a VPN such as [Tailscale](https://tailscale.com/).

**Encrypted channels stay encrypted.** Some agencies encrypt some or all of
their traffic. This system cannot decrypt those, nothing legitimate can, and it
does not try — those calls are logged as encrypted and skipped.

**Transcripts contain mistakes.** Speech recognition on noisy radio audio gets
names, addresses, and unit numbers wrong on a regular basis. Treat a transcript
as a rough index for finding the audio, never as a record of what was said. Go
listen to the call.

**Know the rules where you are.** Receiving these transmissions is legal in
Colorado, but what you may *do* with the recordings — republishing them, in
particular — is a separate question, and rebroadcasting live public-safety audio
is restricted in some circumstances. If you plan to make any of this public,
look into it first.

---

## How it fits together

```
   Antenna
      │
   Airspy R2  ──USB──┐
                     │
        ┌────────────▼────────────┐
        │     trunk-recorder      │  tunes the radio, follows the P25 control
        │                         │  channel, records each call to disk
        └────┬───────────────┬────┘
             │               │
             │        ┌──────▼───────┐
             │        │ rdio-scanner │  :3000  listen, scanner-style
             │        └──────────────┘
             │
      recordings on disk
             │
        ┌────▼─────────┐
        │  transcriber │  Whisper on the GPU turns audio into text
        └────┬─────┬───┘
             │     │
             │     └────────────► Discord (optional)
             │
        ┌────▼───┐
        │  web   │  :8080  read and search transcripts
        └────────┘
```

Four Docker containers, defined in [`docker-compose.yml`](docker-compose.yml).
The transcriber watches the recording folder rather than being wired directly
into the recorder, so if transcription crashes or falls behind, recording keeps
going and catches up on its own.

## Credits

- [Trunk Recorder](https://github.com/TrunkRecorder/trunk-recorder) — the recording engine
- [Rdio Scanner](https://github.com/chuot/rdio-scanner) — the listening interface
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — speech recognition
- [RadioReference](https://www.radioreference.com/) — the system and talkgroup database
