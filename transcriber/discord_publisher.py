"""Post transcribed calls to Discord.

Uses Discord **webhooks** rather than a bot. A webhook is just a URL you copy
out of a channel's settings and paste into .env — no application to register, no
bot token, no invite flow, nothing to keep logged in. A bot would only buy us
things this system does not do (reading messages, slash commands).

Runs on its own thread behind a bounded queue, so a Discord outage, a rate
limit, or a typo'd webhook URL can never slow down or break transcription.

NOTE: this is written against Discord's webhook API directly. Steve has existing
Discord code in the `gjrad` / `gjrad-calls` repos that may use a different
approach (a bot, a different message format, or a different trigger). Those
repos were not reachable from the session that wrote this. Before relying on
this in anger, compare the two and keep whichever is already proven — see
docs/06-discord.md.
"""

import json
import logging
import os
import queue
import threading
import time

import requests

log = logging.getLogger("discord")

# Discord's limits. A plain message's text caps at 2000 characters (an embed
# description would allow 4096, but we do not use embeds). The upload cap is
# what a server without a Nitro boost allows.
MESSAGE_CONTENT_LIMIT = 2000
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

# Queue depth before we start dropping. If Discord is down we would rather lose
# posts than grow this without bound — the calls are all still in the database
# and on disk regardless.
MAX_QUEUE = 500

# Transcripts are machine-generated from radio audio — nothing we control. Text
# in a message body (unlike text in an embed) really does ping people, so every
# post disables mention parsing outright rather than trusting the content.
NO_MENTIONS = {"parse": []}


class DiscordPublisher:
    def __init__(self, store, config_dir, media_root):
        self._store = store
        self._media_root = media_root
        self._config_path = os.path.join(config_dir, "discord.json")
        self._queue = queue.Queue(maxsize=MAX_QUEUE)
        self._stop = threading.Event()
        self._thread = None
        self._session = None
        # webhook url -> earliest time we may post to it again
        self._next_allowed = {}

        self.enabled = os.environ.get("DISCORD_ENABLED", "false").lower() in (
            "1", "true", "yes", "on"
        )
        self._config = self._load_config()

    # -- configuration -------------------------------------------------------

    def _load_config(self):
        defaults = {
            "min_duration_seconds": 1.5,
            "skip_empty_transcripts": True,
            "skip_encrypted": True,
            "attach_audio": True,
            "default_webhook_env": "DISCORD_WEBHOOK_URL",
            "routes": [],
        }
        try:
            with open(self._config_path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
        except FileNotFoundError:
            log.info("no %s - Discord posting disabled", self._config_path)
            return defaults
        except ValueError as exc:
            log.error("%s is not valid JSON (%s) - Discord posting disabled",
                      self._config_path, exc)
            return defaults

        defaults.update({k: v for k, v in loaded.items() if not k.startswith("_")})
        return defaults

    def _webhook_for(self, record):
        """Return the webhook URL for this call, or None to not post it.

        First matching route wins. No match means no post — that is deliberate:
        this site's full traffic would be unreadable in Discord and would hit
        the per-channel rate limit within seconds, so posting is opt-in.
        """
        for route in self._config.get("routes") or []:
            if self._route_matches(route.get("match") or {}, record):
                env_name = route.get("webhook_env") or self._config["default_webhook_env"]
                url = os.environ.get(env_name, "").strip()
                if not url:
                    log.warning("route matched but %s is empty in .env - not posting",
                                env_name)
                    return None
                return url
        return None

    @staticmethod
    def _route_matches(match, record):
        if not match:
            return False

        talkgroups = match.get("talkgroups")
        if talkgroups is not None and record["talkgroup"] not in talkgroups:
            return False

        group = match.get("group")
        if group is not None:
            if (record["talkgroup_group"] or "").lower() != str(group).lower():
                return False

        tag = match.get("tag")
        if tag is not None:
            if (record["talkgroup_group_tag"] or "").lower() != str(tag).lower():
                return False

        return True

    # -- lifecycle -----------------------------------------------------------

    def start(self):
        if not self.enabled:
            log.info("Discord posting is off (DISCORD_ENABLED is not true)")
            return
        if not (self._config.get("routes") or []):
            log.info("Discord is enabled but config/discord.json has no routes, "
                     "so nothing will be posted. See docs/06-discord.md.")
        self._session = requests.Session()
        self._thread = threading.Thread(target=self._run, daemon=True, name="discord")
        self._thread.start()
        log.info("Discord publisher started (%d route(s))",
                 len(self._config.get("routes") or []))

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)

    def enqueue(self, call_id, record):
        if not self.enabled:
            return
        if not self._should_post(record):
            return
        try:
            self._queue.put_nowait((call_id, dict(record)))
        except queue.Full:
            log.warning("Discord queue is full - dropping call %s", record["call_key"])

    def _should_post(self, record):
        cfg = self._config
        if cfg.get("skip_encrypted", True) and record.get("encrypted"):
            return False
        if cfg.get("skip_empty_transcripts", True) and not record.get("transcript"):
            return False
        min_duration = float(cfg.get("min_duration_seconds") or 0)
        if record.get("duration") and record["duration"] < min_duration:
            return False
        return True

    # -- worker --------------------------------------------------------------

    def _run(self):
        while not self._stop.is_set():
            try:
                call_id, record = self._queue.get(timeout=1)
            except queue.Empty:
                continue

            try:
                if self._store.already_posted_to_discord(call_id):
                    continue
                url = self._webhook_for(record)
                if not url:
                    continue
                if self._post(url, record):
                    self._store.mark_discord_posted(call_id)
            except Exception:
                log.exception("failed to post %s to Discord", record.get("call_key"))
            finally:
                self._queue.task_done()

    def _post(self, url, record, attempts=4):
        payload = {
            "content": self._build_content(record),
            "allowed_mentions": NO_MENTIONS,
        }
        audio_path = self._audio_for(record)

        # Discord rejects a message with neither text nor an attachment. Normally
        # unreachable — skip_empty_transcripts stops empty calls earlier — but it
        # is a user-settable option, so do not send a request that must fail.
        if not payload["content"] and not audio_path:
            log.debug("nothing to post for %s (no transcript, no audio)",
                      record.get("call_key"))
            return True

        for attempt in range(1, attempts + 1):
            self._wait_for_rate_limit(url)

            files = None
            handle = None
            try:
                if audio_path:
                    handle = open(audio_path, "rb")
                    files = {
                        "payload_json": (None, json.dumps(payload), "application/json"),
                        "files[0]": (os.path.basename(audio_path), handle, "audio/mp4"),
                    }
                    response = self._session.post(url, files=files, timeout=30)
                else:
                    response = self._session.post(url, json=payload, timeout=30)
            except requests.RequestException as exc:
                log.warning("Discord request failed (attempt %d/%d): %s",
                            attempt, attempts, exc)
                time.sleep(min(2 ** attempt, 30))
                continue
            finally:
                if handle:
                    handle.close()

            self._record_rate_limit(url, response)

            if response.status_code in (200, 204):
                return True

            if response.status_code == 429:
                retry_after = self._retry_after(response)
                log.warning("Discord rate limited us; waiting %.1fs", retry_after)
                time.sleep(retry_after)
                continue

            if 500 <= response.status_code < 600:
                log.warning("Discord returned %d (attempt %d/%d)",
                            response.status_code, attempt, attempts)
                time.sleep(min(2 ** attempt, 30))
                continue

            # 4xx other than 429 will not succeed on retry — a bad or deleted
            # webhook URL, or a malformed message.
            log.error("Discord rejected the post (%d): %s",
                      response.status_code, response.text[:300])
            return False

        log.error("giving up on posting %s to Discord after %d attempts",
                  record.get("call_key"), attempts)
        return False

    def _audio_for(self, record):
        if not self._config.get("attach_audio", True):
            return None

        # Prefer the compressed .m4a: it is a fraction of the size and plays
        # inline in the Discord client.
        base = os.path.splitext(os.path.join(self._media_root, record["audio_path"]))[0]
        for ext in (".m4a", ".wav"):
            path = base + ext
            if not os.path.exists(path):
                continue
            try:
                if os.path.getsize(path) > MAX_UPLOAD_BYTES:
                    continue
            except OSError:
                continue
            return path

        log.debug("no attachable audio for %s - posting text only",
                  record.get("call_key"))
        return None

    # -- rate limiting -------------------------------------------------------

    def _wait_for_rate_limit(self, url):
        deadline = self._next_allowed.get(url)
        if deadline:
            delay = deadline - time.monotonic()
            if delay > 0:
                time.sleep(delay)

    def _record_rate_limit(self, url, response):
        """Pre-emptively pause when Discord says we have used up the bucket."""
        try:
            remaining = int(response.headers.get("X-RateLimit-Remaining", "1"))
            reset_after = float(response.headers.get("X-RateLimit-Reset-After", "0"))
        except ValueError:
            return
        if remaining <= 0 and reset_after > 0:
            self._next_allowed[url] = time.monotonic() + reset_after

    @staticmethod
    def _retry_after(response):
        try:
            body = response.json()
            if "retry_after" in body:
                return float(body["retry_after"])
        except ValueError:
            pass
        try:
            return float(response.headers.get("Retry-After", "5"))
        except ValueError:
            return 5.0

    # -- message formatting --------------------------------------------------

    @staticmethod
    def _build_content(record):
        """The message body: the transcript, and nothing else.

        No talkgroup label, no timestamp, no unit IDs. Routing puts each
        talkgroup in its own channel, so the channel already says what this is;
        repeating it on every message just made the channel harder to read. If
        several talkgroups do share a channel, the attached audio file is named
        after the talkgroup and time.
        """
        transcript = (record.get("transcript") or "").strip()
        if len(transcript) > MESSAGE_CONTENT_LIMIT:
            transcript = transcript[: MESSAGE_CONTENT_LIMIT - 1] + "…"
        return transcript
