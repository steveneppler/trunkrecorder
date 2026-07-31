# 6. Posting to Discord

Calls can be posted into Discord channels — the audio as a playable attachment,
with the transcript alongside.

**Nothing is posted until you set this up.** That is deliberate: this radio site
carries far more traffic than a Discord channel can usefully hold, and posting
all of it would flood the channel and hit Discord's rate limits within seconds.
You choose which talkgroups go where.

> ### Before you rely on this
>
> Steve has existing Discord code in the **`gjrad`** and **`gjrad-calls`**
> repositories that already does some of this. Those repositories were not
> reachable from the session that built this, so what is here was written
> independently against Discord's webhook API.
>
> If `gjrad-calls` already has a working publisher — especially if it uses a
> different trigger, or a bot instead of webhooks — prefer that one and treat
> this as the fallback. Two systems posting to the same server in two different
> formats is worse than either alone.

---

## 6.1 Create a webhook

A **webhook** is a URL that lets a program post into one specific channel. There
is no bot to install and nothing to log into.

In Discord, on a computer (not the phone app):

1. Make sure you have **Manage Webhooks** permission on the server.
2. Right-click the channel you want calls posted to → **Edit Channel**.
3. **Integrations** → **Webhooks** → **New Webhook**.
4. Give it a name — "Scanner" works.
5. Click **Copy Webhook URL**.

You now have a URL like
`https://discord.com/api/webhooks/1234567890/AbCdEf...`

> **Treat this like a password.** Anyone who has it can post to that channel as
> that webhook. It goes in `.env`, which is git-ignored, and never in
> `config/discord.json`.

---

## 6.2 Put the URL in `.env`

```bash
nano .env
```

Turn the feature on and paste your URL:

```bash
DISCORD_ENABLED=true
DISCORD_WEBHOOK_FIRE=https://discord.com/api/webhooks/1234567890/AbCdEf...
```

There are several slots (`_FIRE`, `_LAW`, `_EMS`, `_ROADS`, and a general
`DISCORD_WEBHOOK_URL`) so different kinds of traffic can go to different
channels. Use as many or as few as you like — they are just names.

Save with `Ctrl+O`, `Enter`, `Ctrl+X`.

---

## 6.3 Choose what gets posted

```bash
nano config/discord.json
```

Add rules to the `routes` list:

```json
{
  "min_duration_seconds": 1.5,
  "skip_empty_transcripts": true,
  "skip_encrypted": true,
  "attach_audio": true,

  "default_webhook_env": "DISCORD_WEBHOOK_URL",

  "routes": [
    { "match": { "talkgroups": [8441] }, "webhook_env": "DISCORD_WEBHOOK_FIRE" },
    { "match": { "group": "CDOT" },      "webhook_env": "DISCORD_WEBHOOK_ROADS" }
  ]
}
```

Then apply it:

```bash
docker compose up -d transcriber
```

### How routes work

Rules are checked **top to bottom, and the first one that matches wins.** A call
that matches nothing is not posted.

Three ways to match:

| Match on | Example | Meaning |
| --- | --- | --- |
| `talkgroups` | `{ "talkgroups": [8441, 8442] }` | Specific talkgroup numbers |
| `group` | `{ "group": "Fire" }` | The `Category` column in `talkgroups.csv` |
| `tag` | `{ "tag": "Fire Dispatch" }` | The `Tag` column in `talkgroups.csv` |

`group` and `tag` only work for talkgroups you have named in
[`talkgroups.csv`](04-talkgroups.md) — an unnamed talkgroup has no category to
match against, so match those by number.

You can combine them; all conditions in one `match` must hold.

`webhook_env` names an **environment variable**, not a URL. If you leave it out,
the route uses `default_webhook_env`.

### The other settings

| Setting | What it does |
| --- | --- |
| `min_duration_seconds` | Skip calls shorter than this. Short calls are usually radio clicks. |
| `skip_empty_transcripts` | Do not post calls where nothing was said. Recommended — since the message *is* the transcript, a call with no speech has nothing to show but the audio. |
| `skip_encrypted` | Do not post encrypted calls (there is nothing to hear anyway). |
| `attach_audio` | Attach the audio file. Set `false` for text-only posts. Turning this off while `skip_empty_transcripts` is also off leaves nothing to post for silent calls — those are skipped rather than sent empty. |

---

## 6.4 Check it worked

```bash
docker compose logs -f transcriber
```

On startup you should see:

```
Discord publisher started (2 route(s))
```

Then wait for a call on a routed talkgroup. It will appear in the channel as
**just the transcript**, with the audio attached as a playable clip:

> Engine 1, Medic 3, respond to a structure fire, 2840 Orchard Avenue, cross of
> 28 and a half Road, smoke showing from the second floor.
>
> 🔊 `8441-1785333627_853962500-call_1.m4a`

No talkgroup label, no timestamp, no unit IDs — the channel already tells you
what you are reading, and repeating it on every message just made the channel
harder to skim. If you do route several talkgroups into one channel, the
attachment's filename still starts with the talkgroup number.

**If nothing appears:**

| Log message | What to do |
| --- | --- |
| `Discord posting is off` | `DISCORD_ENABLED` is not `true` in `.env` |
| `no routes, so nothing will be posted` | `routes` in `config/discord.json` is still empty |
| `route matched but DISCORD_WEBHOOK_X is empty` | That variable has no URL in `.env` |
| `Discord rejected the post (401)` | The webhook URL is wrong or the webhook was deleted |
| `Discord rate limited us` | Normal under load — it waits and retries. If constant, post fewer talkgroups. |

Nothing at all in the log means no call matched a route yet. Try a busier
talkgroup.

---

## 6.5 Keeping the channel readable

Discord allows roughly 30 messages per minute per channel. A dispatch talkgroup
during a working incident can exceed that easily.

- **Start with one or two talkgroups.** Dispatch channels are the interesting
  ones; tactical channels are high-volume and mostly context-free out of order.
- **Split across channels.** Fire in one, law in another, roads in a third.
  This matters more than it looks: the messages carry no talkgroup label, so the
  channel is the only thing telling you whose traffic you are reading. Mixing
  several talkgroups into one channel gives you an undifferentiated wall of text.
- **Raise `min_duration_seconds`** to 2 or 3 to drop the short acknowledgements
  ("copy", "10-4") that make up much of the traffic.
- **Leave `skip_empty_transcripts` on.**

The publisher handles rate limits properly — it reads Discord's headers, waits
when told to, and retries — so being throttled degrades gracefully rather than
losing posts. But a channel that is being throttled is a channel nobody can
read.

---

## 6.6 Things worth knowing

**Restarting will not re-post old calls.** Each call is marked once posted, and
the mark is stored in the database, so a restart picks up where it left off.

**Discord posting never slows down transcription.** It runs on its own thread
behind a queue. If Discord is down, transcription carries on and the queue
drains later. If the queue reaches 500 waiting calls it starts dropping them —
the calls themselves are still recorded, transcribed, and searchable on the web
page.

**Large calls post without audio.** Discord caps uploads at 10 MB on servers
without a boost. Compressed call audio is around 32 kbit/s, so a call would need
to run about 40 minutes to hit that. If it happens, the post still goes out with
the transcript, just no attachment.

**Think about who can see the channel.** Posting live public-safety audio into a
public Discord server is a different thing from keeping it for yourself. Some of
what crosses these channels involves people having the worst day of their lives,
and identifying details go out over the air. Keep the server private unless you
have thought carefully about that.

---

**Next:** [7. Troubleshooting](07-troubleshooting.md)
