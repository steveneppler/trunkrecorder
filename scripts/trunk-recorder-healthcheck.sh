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
# This fails when every one of those lines in the last few minutes says 0.
# That is not a quiet night: the control channel transmits continuously
# whether or not anybody is talking, so a working recorder never reads 0.
#
# It ignores lines written before the current Trunk Recorder process started,
# so the zeros that got a broken process restarted do not count against the
# fresh one.
#
# If it finds no such lines at all — `logFile` turned off in config.json, or a
# future Trunk Recorder version wording them differently — it skips this check
# rather than failing, because failing would restart a working scanner every
# few minutes forever.
#
#
# 3. HAS IT GONE SILENT?
#
# Those status blocks are written every 200 seconds regardless of radio
# traffic. If the log has not been written to for several minutes, Trunk
# Recorder is wedged, whatever the reason.
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

# Checks 2 and 3: how far back to look, in seconds. Status blocks come every
# 200 seconds, so the default of 600 always holds at least two of them.
WINDOW="${TR_HEALTH_DECODE_WINDOW:-600}"

# Survives for the life of the container and is thrown away on restart, which
# is what we want: a fresh start should measure a fresh baseline.
PEAK_FILE="${TR_HEALTH_PEAK_FILE:-/tmp/trunk-recorder-health.peak}"

# Where Trunk Recorder writes its log files: `logDir` in config.json.
LOG_DIR="${TR_HEALTH_LOG_DIR:-/app/logs}"

# Which process to measure. Inside the container Trunk Recorder is PID 1, which
# is why that is the default. Overridable so the script can be pointed at a real
# process from the host when testing it.
PID="${TR_HEALTH_PID:-1}"

# The current time, as seconds since 1970. Overridable for the same reason, so
# that a clock change can be tested without waiting for one.
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
  ''|*[!0-9]*) started="$now" ;;    # cannot tell; act as if it just started
  *) started=$(( boot + ticks / hz )) ;;
esac
age=$(( now - started ))

# Too young to have a full window of log behind it. start_period in
# docker-compose.yml covers the first three minutes; this covers the rest.
if [ "$age" -lt "$WINDOW" ]; then
  echo "ok: $thread_status; started ${age}s ago, log checks begin at ${WINDOW}s"
  exit 0
fi

# --- 3. Is it still writing its log? -----------------------------------------
#
# Newest two files, because the log rotates at midnight and the brand-new file
# may not have a status block in it yet.

logs=$(ls -1t "$LOG_DIR" 2>/dev/null | head -n 2)

if [ -z "$logs" ]; then
  echo "ok: $thread_status; no log files in $LOG_DIR, control channel not checked"
  exit 0
fi

newest="$LOG_DIR/$(echo "$logs" | head -n 1)"
written=$(stat -c %Y "$newest" 2>/dev/null || echo "$now")

# Files left over from before this process started say nothing about it, and
# they are all there is if `logFile` has since been turned off. Treating them
# as silence would restart a working recorder every few minutes, forever.
# Trunk Recorder logs its banner the moment it starts, so with logging on the
# newest file is always younger than the process.
if [ "$written" -lt "$started" ]; then
  echo "ok: $thread_status; nothing logged since this process started"
  echo "(logFile off?), control channel not checked"
  exit 0
fi

silent=$(( now - written ))

if [ "$silent" -gt "$WINDOW" ]; then
  echo "unhealthy: Trunk Recorder has written nothing to its log for ${silent}s"
  echo "($newest). It writes a status block every 200s even when the radio is"
  echo "quiet, so it is wedged."
  exit 1
fi

# --- 2. Control channel decode rate ------------------------------------------
#
# Log timestamps are local time in the form [2026-10-23 00:13:16.008160], which
# sorts as text, so a plain string comparison against the cutoff works.
#
# Except across a clock change, because the timestamps carry no UTC offset. In
# the autumn 01:00-02:00 happens twice, and for the first WINDOW seconds after
# the clocks go back the cutoff reads 01:5x while the newest lines read 01:0x.
# Those newest lines would be thrown away, hiding a recovery and possibly
# restarting a recorder that is working. So while the window straddles a
# change of offset, this check stands down. (For the rest of the repeated hour,
# lines from the first pass through it count as recent too. That can delay a
# restart but never cause one.)
#
# The process is at least WINDOW seconds old by now, so this cutoff also
# excludes anything a previous process wrote.

since=$(( now - WINDOW ))
if [ "$(date -d "@$since" +%z)" != "$(date -d "@$now" +%z)" ]; then
  echo "ok: $thread_status; clocks changed in the last ${WINDOW}s,"
  echo "control channel not checked"
  exit 0
fi
cutoff=$(date -d "@$since" '+%Y-%m-%d %H:%M:%S')

rates=$(
  for f in $logs; do
    tail -n 5000 "$LOG_DIR/$f" 2>/dev/null
  done | awk -v cutoff="$cutoff" '
    {
      gsub(/\033\[[0-9;]*m/, "")                    # strip colour codes
      if (substr($0, 2, 19) < cutoff) next
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

if [ "$readings" -eq 0 ]; then
  echo "ok: $thread_status; no control channel status lines since $cutoff,"
  echo "control channel not checked"
  exit 0
fi

if [ "$best" -eq 0 ]; then
  echo "unhealthy: control channel decoded 0 msg/sec in all $readings status"
  echo "lines since $cutoff. Trunk Recorder is running but has lost the control"
  echo "channel, so it cannot record anything. A restart usually recovers it;"
  echo "if it keeps happening, check the antenna and docs/07-troubleshooting.md."
  exit 1
fi

echo "ok: $thread_status; control channel up to $best msg/sec since $cutoff"
exit 0
