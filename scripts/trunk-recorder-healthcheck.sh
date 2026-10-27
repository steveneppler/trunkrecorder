#!/bin/sh
#
# Is Trunk Recorder still actually working?
#
# This runs INSIDE the trunk-recorder container, once a minute, because
# docker-compose.yml points its `healthcheck:` at it. You never run this
# yourself. To see what it most recently decided:
#
#     docker inspect --format '{{json .State.Health}}' trunk-recorder | jq
#
# It makes three checks, and fails if any of them does. Each one exists because
# Trunk Recorder has, at some point, stopped being a scanner without exiting —
# so `restart: unless-stopped` never fired, because a restart policy reacts to a
# process EXITING and this process was still very much alive.
#
#
# 1. HAS IT LOST ITS WORKER THREADS?
#
# On 2026-09-13 the Airspy briefly dropped off USB and came back a fraction of
# a second later as a new device:
#
#     usb 1-5: USB disconnect, device number 2
#     usb 1-5: new high-speed USB device number 3 using xhci_hcd
#
# Trunk Recorder tore down its GNU Radio worker threads, went to 0% CPU, and
# sat there doing nothing for fifteen hours until the machine was rebooted by
# hand.
#
# The giveaway is the thread count. Trunk Recorder runs one thread per GNU
# Radio block, so a working recorder has a lot of them and a dead one has
# almost none. On the machine this was written for: 209 threads healthy, 16
# after the Airspy vanished.
#
# The healthy number depends on how many `digitalRecorders` you configured, so
# there is no single correct value to hard-code. Instead this remembers the
# highest thread count it has seen since the container started, and complains
# when the current count falls to a fraction of that. To see your own number:
#
#     docker exec trunk-recorder sh -c 'ls /proc/1/task | wc -l'
#
#
# 2. IS IT STILL DECODING THE CONTROL CHANNEL?
#
# On 2026-10-23 at 00:05 the control channel signal faded — 40 messages a
# second, then 19, then 5 — and by 00:07 Trunk Recorder was hopping between
# the site's control channel frequencies every three seconds looking for it.
# It never found it again. It decoded exactly 0 msg/sec for the next three and
# a half days, with every one of its threads still alive, so check 1 saw
# nothing wrong. A plain `docker restart` fixed it instantly: the fresh process
# was decoding 40 msg/sec within seconds, on the same antenna and frequencies.
#
# Every 200 seconds Trunk Recorder writes a status block to its log, including
# a line like this for each system:
#
#     [gjdtrs]	852.562500 MHz	40 msg/sec
#
# This fails when none of those lines has been above 0 for several minutes.
# That is not a quiet night: the control channel transmits continuously
# whether or not anybody is talking, so a working recorder never reads 0.
#
#
# 3. HAS IT STOPPED REPORTING?
#
# Those status blocks are written every 200 seconds regardless of radio
# traffic. If none has appeared for several minutes, Trunk Recorder is wedged,
# whatever the reason.
#
#
# HOW CHECKS 2 AND 3 READ THE LOG
#
# Each probe reads only what was added to the log since the previous one, and
# remembers when it last saw a status line and when it last saw one above 0.
# Those times come from this machine's clock, not from the timestamps in the
# log, which have no UTC offset and so cannot be compared across a clock
# change. Reading by position also means there is no line limit for a reading
# to fall off the end of, however chatty the log level.
#
# Neither check does anything until a status line from the current process
# has been seen. With `logFile` off, `logLevel` set above info, or a future
# Trunk Recorder version that words those lines differently, none ever is, and
# both are skipped rather than failing — failing would restart a working
# scanner every few minutes forever. The thread check still runs.
#
#
# The tunables below can be overridden in .env if you ever need to. You almost
# certainly will not.

set -u

# Check 1: fail when the thread count drops below this percentage of the
# highest count seen so far. 40% is deliberately loose: the real failure is a
# fall of ~92%, so there is an enormous margin before a false alarm is possible.
RATIO="${TR_HEALTH_MIN_RATIO:-40}"

# Check 1: a hard floor, used while the peak is still being established. It
# catches the case where the SDR dies during startup, before a healthy peak was
# ever recorded — without it, a container that came up broken would look
# "fine" forever because its peak would be just as low as its current count.
FLOOR="${TR_HEALTH_MIN_THREADS:-20}"

# Checks 2 and 3: how long, in seconds, without a status line above 0 msg/sec
# (or without any status line) before failing. Status blocks come every 200
# seconds, so the default of 600 always spans at least two of them.
WINDOW="${TR_HEALTH_DECODE_WINDOW:-600}"

# Lives in /tmp, which keeps it for the life of the container — including
# across `docker restart`, which stops and starts the same container rather
# than recreating it. That is fine: the same hardware and config reach the
# same peak after a restart.
PEAK_FILE="${TR_HEALTH_PEAK_FILE:-/tmp/trunk-recorder-health.peak}"

# Checks 2 and 3: where each probe leaves its place in the log and what it saw
# for the next one.
STATE_FILE="${TR_HEALTH_STATE_FILE:-/tmp/trunk-recorder-health.state}"

# Where Trunk Recorder writes its log files: `logDir` in config.json.
LOG_DIR="${TR_HEALTH_LOG_DIR:-/app/logs}"

# Which process to measure. Inside the container Trunk Recorder is PID 1, which
# is why that is the default. Overridable so the script can be pointed at a real
# process from the host when testing it.
PID="${TR_HEALTH_PID:-1}"

# The current time, as seconds since 1970. Overridable for the same reason, so
# that a run of probes minutes apart can be tested without waiting for them.
NOW="${TR_HEALTH_NOW:-}"

# --- 1. Threads --------------------------------------------------------------

threads=$(ls "/proc/$PID/task" 2>/dev/null | wc -l)

if [ "$threads" -eq 0 ]; then
  echo "cannot read /proc/$PID/task — is Trunk Recorder running?"
  exit 1
fi

peak=$(cat "$PEAK_FILE" 2>/dev/null || echo 0)
case "$peak" in
  ''|*[!0-9]*) peak=0 ;;     # unreadable or corrupt, start over
esac

if [ "$threads" -gt "$peak" ]; then
  peak="$threads"
  # Subshell so that a failure to open the file is swallowed too, not just a
  # failure to write to it. If this cannot be saved we simply re-measure the
  # peak next time round, which is harmless.
  ( echo "$peak" > "$PEAK_FILE" ) 2>/dev/null || true
fi

threshold=$(( peak * RATIO / 100 ))
[ "$threshold" -lt "$FLOOR" ] && threshold="$FLOOR"

if [ "$threads" -lt "$threshold" ]; then
  echo "unhealthy: $threads threads, expected at least $threshold (peak $peak)"
  echo "Trunk Recorder is running but has lost its worker threads — usually"
  echo "means the SDR disappeared. Check: dmesg | grep -i usb"
  exit 1
fi

thread_status="$threads threads (peak $peak, threshold $threshold)"

# --- When did this Trunk Recorder process start? -----------------------------
#
# Field 22 of /proc/PID/stat is the start time in clock ticks since boot, and
# /proc/stat has the boot time. The ")" split skips the process name, which is
# allowed to contain spaces.

now="${NOW:-$(date +%s)}"
boot=$(awk '/^btime/ { print $2 }' /proc/stat 2>/dev/null)
ticks=$(sed 's/.*) //' "/proc/$PID/stat" 2>/dev/null | awk '{ print $20 }')
hz=$(getconf CLK_TCK 2>/dev/null || echo 100)
case "$boot$ticks$hz" in
  ''|*[!0-9]*) started=unknown ;;
  *) started=$(( boot + ticks / hz )) ;;
esac

# --- What did the previous probe leave us? -----------------------------------
#
# The state belongs to one Trunk Recorder process. /tmp survives `docker
# restart` — the container is stopped and started, not recreated — so a state
# file from before a restart is still there, and is thrown away here because
# its process start time does not match.

state() { awk -v k="$1" '$1 == k { print $2; exit }' "$STATE_FILE" 2>/dev/null; }
offset_of() { awk -v f="$1" '$1 == "file" && $2 == f { print $3; exit }' "$STATE_FILE" 2>/dev/null; }

fresh=1
if [ "$(state started)" = "$started" ]; then
  seen=$(state seen); last_status=$(state status); last_good=$(state good)
  case "$seen$last_status$last_good" in
    ''|*[!0-9]*) ;;               # unreadable or corrupt, start over
    *) fresh=0 ;;
  esac
fi
if [ "$fresh" -eq 1 ]; then
  # Counting from now gives a new process a full WINDOW before it can fail.
  seen=0; last_status=$now; last_good=$now
fi

# --- Read what the log gained since the previous probe -----------------------
#
# Newest two files, because the log rotates at midnight and the tail end of
# the old file has not been read yet.
#
# A fresh state starts at the end of each file, so nothing a previous process
# wrote is ever counted. A file the state has not seen before is new since the
# previous probe, so it is read from the beginning.

reads=""
for f in $(ls -1t "$LOG_DIR" 2>/dev/null | head -n 2); do
  size=$(stat -c %s "$LOG_DIR/$f" 2>/dev/null) || continue
  from=$(offset_of "$f")
  if [ "$fresh" -eq 1 ]; then
    from=$size
  elif [ -z "$from" ] || [ "$from" -gt "$size" ]; then
    from=0                        # new file, or it was truncated
  fi
  reads="$reads$f $from $size
"
done

rates=$(
  echo "$reads" | while read -r f from size; do
    [ -n "$f" ] || continue
    tail -c +$(( from + 1 )) "$LOG_DIR/$f" 2>/dev/null | head -c $(( size - from ))
  done | awk '
    {
      gsub(/\033\[[0-9;]*m/, "")                    # strip colour codes
      if (!match($0, /MHz[ \t]+[0-9]+ msg\/sec/)) next
      s = substr($0, RSTART, RLENGTH)
      sub(/^MHz[ \t]+/, "", s)
      sub(/ msg\/sec$/, "", s)
      n++
      if (s + 0 > best) best = s + 0
    }
    END { print n + 0, best + 0 }'
)
readings=${rates% *}
best=${rates#* }

[ "$readings" -gt 0 ] && { seen=1; last_status=$now; }
[ "$best" -gt 0 ] && last_good=$now

# Subshell for the same reason as the peak file. Written whole and then moved,
# so a probe killed by its timeout cannot leave half a state behind.
(
  {
    echo "started $started"
    echo "seen $seen"
    echo "status $last_status"
    echo "good $last_good"
    echo "$reads" | awk 'NF == 3 { print "file", $1, $3 }'
  } > "$STATE_FILE.new" && mv "$STATE_FILE.new" "$STATE_FILE"
) 2>/dev/null || true

# --- 2 and 3. Decide ---------------------------------------------------------

if [ "$seen" -eq 0 ]; then
  echo "ok: $thread_status; no control channel status lines seen yet, so it"
  echo "is not checked (they come every 200s at logLevel info, in $LOG_DIR)"
  exit 0
fi

quiet=$(( now - last_status ))
dead=$(( now - last_good ))

if [ "$quiet" -gt "$WINDOW" ]; then
  echo "unhealthy: no status block in Trunk Recorder's log for ${quiet}s. It"
  echo "writes one every 200s even when the radio is quiet, so it is wedged."
  exit 1
fi

if [ "$dead" -gt "$WINDOW" ]; then
  echo "unhealthy: control channel has decoded 0 msg/sec for ${dead}s."
  echo "Trunk Recorder is running but has lost the control channel, so it"
  echo "cannot record anything. A restart usually recovers it; if it keeps"
  echo "happening, check the antenna and docs/07-troubleshooting.md."
  exit 1
fi

if [ "$best" -gt 0 ]; then
  echo "ok: $thread_status; control channel at $best msg/sec"
else
  echo "ok: $thread_status; control channel last above 0 msg/sec ${dead}s ago"
fi
exit 0
