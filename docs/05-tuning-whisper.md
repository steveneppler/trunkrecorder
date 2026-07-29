# 5. Tuning transcription

Two things can go wrong with transcription: it can be too slow, or the text can
be wrong. This page covers both.

All settings here live in `.env`. After editing:

```bash
docker compose up -d transcriber
```

---

## Is it keeping up?

```bash
docker compose logs -f transcriber
```

Each call logs a line like:

```
[GJ Fire Disp] 6.4s in 2.1s rtf 0.33  Engine 1, Medic 3, respond to a structure...
```

**`rtf`** — real-time factor — is the number to watch. It is seconds of computer
time per second of audio.

| rtf | Meaning |
| --- | --- |
| under 0.3 | Lots of headroom |
| 0.3 – 0.6 | Comfortable |
| 0.6 – 1.0 | Keeping up, but a busy afternoon will build a backlog |
| over 1.0 | Falling behind permanently |

If a backlog develops you will see:

```
73 calls waiting - transcription is falling behind.
```

Nothing is lost when this happens — calls are recorded regardless, and the
transcriber works through the queue oldest-first. But transcripts will lag
further and further behind real time.

---

## Making it faster

Try these in order. The first one gives the biggest win for the smallest cost.

### 1. Use a faster model

In `.env`:

```bash
WHISPER_MODEL=distil-large-v3
```

| Model | Speed | Quality on scanner audio |
| --- | --- | --- |
| `large-v3` | baseline | Best — the default |
| `large-v3-turbo` | ~4× faster | Very close to large-v3 |
| `distil-large-v3` | ~6× faster | Slightly worse, still good |
| `medium` | ~2× faster | Noticeably worse |
| `small` | ~6× faster | Only for machines with no GPU |

`large-v3-turbo` is usually the sweet spot when `large-v3` is too slow.

Changing models downloads new weights the first time (1–3 GB).

### 2. Reduce the beam size

```bash
WHISPER_BEAM_SIZE=5
```

This controls how many alternative transcriptions Whisper considers. Going from
10 to 5 is roughly 30–40% faster and the difference in output on radio audio is
small. `1` is faster still and noticeably worse.

### 3. Skip more calls

```bash
MIN_CALL_SECONDS=2.0
```

Very short transmissions are usually a squelch tail or a click with no speech.
Raising this skips them entirely.

You can also skip whole talkgroups:

```bash
SKIP_TALKGROUPS=8020,8023
```

---

## Check your actual numbers

The [whisperbench](https://github.com/steveneppler/whisperbench) repo measures
this properly, on your own hardware, with your own audio.

Point it at a real recorded call rather than the sample clip — scanner audio is
short, noisy, and 8 kHz, which is nothing like clean speech, and it behaves
differently:

```bash
git clone https://github.com/steveneppler/whisperbench.git
cd whisperbench
python3 -m venv .venv
source .venv/bin/activate
pip install faster-whisper psutil

# Pick any recorded call
ls ~/trunkrecorder/media/gjdtrs/*/*/*/*.wav | head

python whisper_benchmark.py \
  --audio ~/trunkrecorder/media/gjdtrs/2026/7/29/8441-1785333627_853962500-call_1.wav \
  --device cuda --compute-type int8 --model large-v3
```

Then try `--model large-v3-turbo` and `--beam-size 5` and compare. The settings
in this project's `.env` match whisperbench's defaults, so the numbers carry
over directly.

---

## A note on your graphics card

**If you have a GTX 10-series card (1070, 1080, etc.):** leave

```bash
WHISPER_COMPUTE_TYPE=int8
```

alone.

These are "Pascal" generation cards. They do not have the fast half-precision
math that newer cards do, and the underlying library requires a newer generation
before it will use `float16`. Asking for it anyway does not fail loudly — it
quietly falls back to full 32-bit precision, which uses twice the memory and
runs considerably slower. `int8` is both the fastest and the most
memory-efficient option on these cards, and the accuracy cost on radio audio is
negligible.

The transcriber warns you in its log if you set `float16` on a GPU that cannot
use it.

**On an RTX card (20-series or newer)**, switch to:

```bash
WHISPER_COMPUTE_TYPE=float16
```

---

## Making it more accurate

### Understand the ceiling

Trunked radio audio is genuinely hard: 8 kHz sample rate, aggressive vocoder
compression, background noise, radio jargon, overlapping speech, and proper
nouns Whisper has never encountered. Expect names, street addresses, and unit
numbers to be wrong regularly. Transcripts are for *finding* calls, not for
quoting them.

### Add local vocabulary

The biggest lever you actually control is the prompt — a hint about what to
expect, which strongly influences how Whisper spells proper nouns.

Edit `transcriber/transcriber.py` and find `INITIAL_PROMPT`. It already lists
Mesa County, Grand Junction, Fruita, Palisade, Clifton, CDOT, I-70, and others.
Add the ones you hear that come out wrong — street names, agency names, hospital
names, common unit designators.

Then rebuild:

```bash
docker compose build transcriber
docker compose up -d transcriber
```

Keep it under a couple of hundred words. An overlong prompt starts to hurt.

### Fix the audio, not the model

If transcripts are gibberish rather than merely imperfect, the problem is
upstream. Play the call on the transcript page — if it sounds garbled to you, no
model will do better. Go back to
[section 3.2](03-first-run.md#32-if-the-decode-rate-is-low) and improve
reception.

### Hallucinations

Whisper invents text when given silence or noise. On a scanner it reaches for
"Thank you.", "Bye.", and — genuinely — "Subtitles by the amara.org community".

These are filtered out already (`HALLUCINATION_PHRASES` in `transcriber.py`). If
you see a new one repeatedly, add it to that list and rebuild.

---

**Next:** [6. Posting to Discord](06-discord.md)
