#!/usr/bin/env python3
"""Transcribe Trunk Recorder calls with Whisper on the GPU.

How this fits together
----------------------
Trunk Recorder finishes a call and writes three files side by side:

    12345-1753700000_852562500-call_42.json   metadata (written first)
    12345-1753700000_852562500-call_42.wav    full-quality audio
    12345-1753700000_852562500-call_42.m4a    compressed audio

This service watches the recording directory, transcribes each new call, stores
the result in SQLite, and hands it to the Discord publisher.

It deliberately does *not* hook into Trunk Recorder directly. Watching a
directory means a slow, crashed, or restarted transcriber can never stall or
break the recorder, and anything recorded while this service was down gets
picked up automatically when it comes back.
"""

import json
import logging
import os
import signal
import sys
import threading
import time

from db import CallStore
import discord_publisher

# --- configuration ---------------------------------------------------------

MEDIA_ROOT = os.environ.get("MEDIA_ROOT", "/media")
CONFIG_DIR = os.environ.get("CONFIG_DIR", "/config")
MODEL_DIR = os.environ.get("MODEL_DIR", "/models")

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "large-v3")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
WHISPER_BEAM_SIZE = int(os.environ.get("WHISPER_BEAM_SIZE", "10"))

MIN_CALL_SECONDS = float(os.environ.get("MIN_CALL_SECONDS", "1.0"))
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL_SECONDS", "2"))
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "30"))

SKIP_TALKGROUPS = {
    int(t) for t in os.environ.get("SKIP_TALKGROUPS", "").replace(" ", "").split(",") if t
}

# A call's audio must be untouched for this long before we read it. Trunk
# Recorder writes the JSON *before* it renders the audio, so the JSON showing up
# is not proof the .wav is finished.
SETTLE_SECONDS = float(os.environ.get("SETTLE_SECONDS", "4"))

# Only look inside recording folders touched in the last two days. Without this
# the poller would re-list every file ever recorded, every couple of seconds.
RECENT_HORIZON_SECONDS = 48 * 3600

# Transcription settings, matching the whisperbench repo so its benchmark
# numbers predict what this box will actually do.
VAD_PARAMETERS = {
    "min_speech_duration_ms": 500,
    "threshold": 0.5,
    "max_speech_duration_s": 30,
}

INITIAL_PROMPT = (
    "Transcribe these police, sheriff, fire, and emergency services radio "
    "transmissions in the Grand Junction, Colorado area. Place and agency "
    "names include Mesa County, Grand Junction, Fruita, Palisade, Clifton, "
    "Whitewater, Loma, Mack, Collbran, De Beque, Delta, Montrose, Redlands, "
    "Orchard Mesa, CDOT, St. Mary's, Community Hospital, Interstate 70, "
    "Highway 50, Highway 340, and mile marker."
)

# Whisper invents text when handed silence or noise. These are the phrases it
# reaches for most often; on a scanner feed they are always hallucinations.
HALLUCINATION_PHRASES = {
    "thank you.", "thanks for watching!", "thank you for watching.",
    "you", "bye.", "bye bye.", ".", "so", "okay.",
    "subtitles by the amara.org community",
    "please subscribe to my channel.",
}

log = logging.getLogger("transcriber")


# --- media directory scanning ----------------------------------------------

def iter_call_dirs(media_root, only_recent, cutoff):
    """Yield (path, entries) for each leaf folder that holds recordings.

    Trunk Recorder lays calls out as <system>/<year>/<month>/<day>/, so the leaf
    folders are the ones with no subdirectories. Old leaves are skipped before
    their file lists are read, which is the whole point — a busy day folder holds
    thousands of files and we poll every couple of seconds.
    """
    stack = [media_root]
    while stack:
        path = stack.pop()
        try:
            entries = list(os.scandir(path))
        except OSError:
            continue

        subdirs = [e for e in entries if e.is_dir(follow_symlinks=False)]
        if subdirs:
            stack.extend(e.path for e in subdirs)
            continue

        if only_recent:
            try:
                if os.stat(path).st_mtime < cutoff:
                    continue
            except OSError:
                continue
        yield path, entries


def find_new_calls(media_root, known_keys, only_recent):
    """Return call keys and JSON paths for calls we have not handled yet."""
    cutoff = time.time() - RECENT_HORIZON_SECONDS
    settle_before = time.time() - SETTLE_SECONDS
    found = []

    for dirpath, entries in iter_call_dirs(media_root, only_recent, cutoff):
        for entry in entries:
            name = entry.name
            if not name.endswith(".json") or name.endswith("-transcript.json"):
                continue

            json_path = entry.path
            stem = json_path[: -len(".json")]
            call_key = os.path.relpath(stem, media_root)
            if call_key in known_keys:
                continue

            audio_path = pick_audio_file(stem)
            if audio_path is None:
                # Audio is still being rendered, or the call failed and Trunk
                # Recorder cleaned up. Either way, try again next pass.
                continue
            try:
                if os.stat(audio_path).st_mtime > settle_before:
                    continue  # still being written
            except OSError:
                continue

            found.append((call_key, json_path, stem, audio_path))

    # Oldest first, so a backlog drains in the order it happened.
    found.sort(key=lambda item: item[0])
    return found


def pick_audio_file(stem):
    """Prefer the .wav — the .m4a is compressed to 32 kbit/s and transcribes worse."""
    for ext in (".wav", ".m4a"):
        path = stem + ext
        if os.path.exists(path):
            return path
    return None


# --- per-call handling ------------------------------------------------------

def load_call_metadata(json_path):
    with open(json_path, "r", encoding="utf-8", errors="replace") as fh:
        return json.load(fh)


def build_record(call_key, meta, json_path, audio_path):
    """Flatten Trunk Recorder's call JSON into the shape the database wants."""
    duration = meta.get("call_length_ms")
    duration = (duration / 1000.0) if duration else float(meta.get("call_length") or 0)

    sources = []
    for src in meta.get("srcList") or []:
        unit = src.get("src")
        if unit and unit not in sources:
            sources.append(unit)

    return {
        "call_key": call_key,
        "short_name": meta.get("short_name"),
        "talkgroup": meta.get("talkgroup"),
        # Not a database column — carried on the record so the Discord publisher
        # can name its attachments. CallStore.insert() ignores extra keys.
        "call_num": meta.get("call_num"),
        "talkgroup_tag": meta.get("talkgroup_tag") or "",
        "talkgroup_description": meta.get("talkgroup_description") or "",
        "talkgroup_group": meta.get("talkgroup_group") or "",
        "talkgroup_group_tag": meta.get("talkgroup_group_tag") or "",
        "start_time": meta.get("start_time"),
        "duration": duration,
        "freq": meta.get("freq"),
        "emergency": int(meta.get("emergency") or 0),
        "encrypted": int(meta.get("encrypted") or 0),
        "sources": sources,
        # Stored relative to the media root so the web service can serve them
        # regardless of where the volume is mounted.
        "audio_path": os.path.relpath(audio_path, MEDIA_ROOT),
        "json_path": os.path.relpath(json_path, MEDIA_ROOT),
        "transcript": "",
        "status": "done",
        "status_detail": None,
        "rtf": None,
        "transcribed_at": None,
    }


def skip_reason(record):
    """Why we should not spend GPU time on this call, or None to transcribe it."""
    if record["encrypted"]:
        return "encrypted"
    if record["duration"] and record["duration"] < MIN_CALL_SECONDS:
        return f"shorter than {MIN_CALL_SECONDS}s"
    if record["talkgroup"] in SKIP_TALKGROUPS:
        return "talkgroup in SKIP_TALKGROUPS"
    return None


def clean_transcript(text):
    """Drop Whisper's stock hallucinations and pure-repetition output."""
    text = " ".join(text.split()).strip()
    if not text:
        return ""
    if text.lower().strip() in HALLUCINATION_PHRASES:
        return ""
    # "you you you you ..." and similar loops.
    words = text.lower().replace(".", "").replace(",", "").split()
    if len(words) >= 4 and len(set(words)) == 1:
        return ""
    return text


def write_sidecar(stem, record):
    """Drop a transcript next to the audio, for anything that reads the files directly."""
    payload = {
        "call_key": record["call_key"],
        "talkgroup": record["talkgroup"],
        "talkgroup_tag": record["talkgroup_tag"],
        "start_time": record["start_time"],
        "duration": record["duration"],
        "transcript": record["transcript"],
        "status": record["status"],
        "model": WHISPER_MODEL,
    }
    tmp = stem + "-transcript.json.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, stem + "-transcript.json")
    except OSError as exc:
        log.warning("could not write transcript sidecar for %s: %s",
                    record["call_key"], exc)


# --- the Whisper model ------------------------------------------------------

class Transcriber:
    def __init__(self):
        from faster_whisper import WhisperModel

        log.info("loading Whisper model %s (device=%s, compute_type=%s)",
                 WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE)
        if WHISPER_DEVICE == "cuda" and WHISPER_COMPUTE_TYPE == "float16":
            log.warning(
                "compute type float16 needs a newer GPU than the GTX 10-series. "
                "On a 1070 this silently falls back to float32 (slower, twice "
                "the memory) - set WHISPER_COMPUTE_TYPE=int8 in your .env file."
            )

        started = time.perf_counter()
        self.model = WhisperModel(
            WHISPER_MODEL,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE_TYPE,
            download_root=MODEL_DIR,
        )
        log.info("model ready in %.1fs", time.perf_counter() - started)

    def transcribe(self, audio_path):
        """Return (text, seconds_spent). Raises on failure."""
        started = time.perf_counter()
        segments, _info = self.model.transcribe(
            audio_path,
            beam_size=WHISPER_BEAM_SIZE,
            language="en",
            initial_prompt=INITIAL_PROMPT,
            vad_filter=True,
            vad_parameters=dict(VAD_PARAMETERS),
            # Each call is independent, and letting Whisper condition on the
            # previous segment is what sends it into repetition loops on the
            # short, noisy audio a scanner produces.
            condition_on_previous_text=False,
        )
        text = " ".join(segment.text.strip() for segment in segments)
        return clean_transcript(text), time.perf_counter() - started


# --- retention --------------------------------------------------------------

def run_retention(store, stop_event):
    """Delete old recordings so the disk does not fill up.

    A busy P25 site produces several GB a week. Without this the server
    eventually wedges on a full disk, which is a miserable thing to debug.
    """
    if RETENTION_DAYS <= 0:
        log.warning("RETENTION_DAYS is 0 - recordings will never be deleted. "
                    "Keep an eye on free disk space.")
        return

    while not stop_event.is_set():
        try:
            cutoff = int(time.time() - RETENTION_DAYS * 86400)
            doomed = store.rows_older_than(cutoff)
            removed_files = 0
            for row in doomed:
                for rel in (row["audio_path"], row["json_path"]):
                    if not rel:
                        continue
                    stem = os.path.join(MEDIA_ROOT, rel)
                    base = os.path.splitext(stem)[0]
                    for path in (stem, base + "-transcript.json"):
                        try:
                            os.remove(path)
                            removed_files += 1
                        except FileNotFoundError:
                            pass
                        except OSError as exc:
                            log.warning("could not delete %s: %s", path, exc)
            store.delete_rows([row["id"] for row in doomed])
            if doomed:
                log.info("retention: removed %d calls (%d files) older than %d days",
                         len(doomed), removed_files, RETENTION_DAYS)
            prune_empty_dirs(MEDIA_ROOT)
        except Exception:
            log.exception("retention pass failed")

        stop_event.wait(3600)


def prune_empty_dirs(media_root):
    for dirpath, dirnames, filenames in os.walk(media_root, topdown=False):
        if dirpath == media_root or dirnames or filenames:
            continue
        try:
            os.rmdir(dirpath)
        except OSError:
            pass


# --- main loop --------------------------------------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    log.info("watching %s", MEDIA_ROOT)
    os.makedirs(MEDIA_ROOT, exist_ok=True)

    store = CallStore()
    known_keys = store.known_call_keys()
    log.info("%d calls already in the database", len(known_keys))

    stop_event = threading.Event()

    def handle_signal(signum, _frame):
        log.info("signal %s received, shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    publisher = discord_publisher.DiscordPublisher(store, CONFIG_DIR, MEDIA_ROOT)
    publisher.start()

    threading.Thread(target=run_retention, args=(store, stop_event),
                     daemon=True, name="retention").start()

    transcriber = Transcriber()

    # The first pass sweeps the whole archive so anything recorded while this
    # service was down gets caught up; after that only recent folders are read.
    first_pass = True

    while not stop_event.is_set():
        try:
            pending = find_new_calls(MEDIA_ROOT, known_keys, only_recent=not first_pass)
            if first_pass:
                log.info("startup scan found %d call(s) to catch up on", len(pending))
                first_pass = False

            if len(pending) > 50:
                log.warning(
                    "%d calls waiting - transcription is falling behind. Consider "
                    "WHISPER_MODEL=distil-large-v3 or WHISPER_BEAM_SIZE=5 "
                    "(see docs/05-tuning-whisper.md).", len(pending)
                )

            for call_key, json_path, stem, audio_path in pending:
                if stop_event.is_set():
                    break
                process_call(transcriber, store, publisher, known_keys,
                             call_key, json_path, stem, audio_path)

            if not pending:
                stop_event.wait(POLL_INTERVAL)
        except Exception:
            log.exception("polling pass failed; retrying")
            stop_event.wait(POLL_INTERVAL)

    log.info("stopping")
    publisher.stop()
    store.close()


def process_call(transcriber, store, publisher, known_keys,
                 call_key, json_path, stem, audio_path):
    known_keys.add(call_key)

    try:
        meta = load_call_metadata(json_path)
    except (OSError, ValueError) as exc:
        log.warning("unreadable call metadata %s: %s", call_key, exc)
        known_keys.discard(call_key)  # let it retry — likely a partial write
        return

    record = build_record(call_key, meta, json_path, audio_path)
    label = record["talkgroup_tag"] or str(record["talkgroup"])

    reason = skip_reason(record)
    if reason:
        record["status"] = "skipped"
        record["status_detail"] = reason
        log.info("[%s] %.1fs  skipped (%s)", label, record["duration"], reason)
    else:
        try:
            text, elapsed = transcriber.transcribe(audio_path)
        except Exception as exc:
            log.exception("transcription failed for %s", call_key)
            record["status"] = "error"
            record["status_detail"] = str(exc)
        else:
            record["transcript"] = text
            record["rtf"] = (elapsed / record["duration"]) if record["duration"] else None
            record["transcribed_at"] = int(time.time())
            rtf_note = f" rtf {record['rtf']:.2f}" if record["rtf"] else ""
            log.info("[%s] %.1fs in %.1fs%s  %s", label, record["duration"],
                     elapsed, rtf_note, text or "(no speech)")

    call_id = store.insert(record)
    if call_id is None:
        return  # already recorded by an earlier run

    write_sidecar(stem, record)

    if record["status"] == "done":
        publisher.enqueue(call_id, record)


if __name__ == "__main__":
    sys.exit(main())
