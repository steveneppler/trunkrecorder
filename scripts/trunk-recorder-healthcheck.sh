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
#
# WHAT IT IS LOOKING FOR
#
# On 2026-09-13 the Airspy briefly dropped off USB and came back a fraction of
# a second later as a new device:
#
#     usb 1-5: USB disconnect, device number 2
#     usb 1-5: new high-speed USB device number 3 using xhci_hcd
#
# Trunk Recorder did not crash and it did not exit. It tore down its GNU Radio
# worker threads, went to 0% CPU, and sat there doing nothing for fifteen hours
# until the machine was rebooted by hand. Because the process never exited,
# `restart: unless-stopped` never fired — a restart policy reacts to a process
# EXITING, and this process was still very much alive. It just wasn't a scanner
# any more.
#
# The giveaway is the thread count. Trunk Recorder runs one thread per GNU
# Radio block, so a working recorder has a lot of them and a dead one has
# almost none. On the machine this was written for: 209 threads healthy, 16
# after the Airspy vanished. That is not a subtle difference.
#
# We use the thread count rather than "has a call been recorded recently?"
# because a quiet night is not a fault. Thread count does not care whether
# anyone is talking on the radio.
#
#
# HOW THE THRESHOLD IS CHOSEN
#
# The healthy number depends on how many `digitalRecorders` you configured, so
# there is no single correct value to hard-code. Instead this remembers the
# highest thread count it has ever seen since the container started, and
# complains when the current count falls to a fraction of that. Your own
# baseline sets your own threshold.
#
# To see your healthy number:
#
#     docker exec trunk-recorder sh -c 'ls /proc/1/task | wc -l'
#
# The two tunables below can be overridden in .env if you ever need to. You
# almost certainly will not.

set -u

# Fail when the thread count drops below this percentage of the highest count
# seen so far. 40% is deliberately loose: the real failure is a fall of ~92%,
# so there is an enormous margin before a false alarm is possible.
RATIO="${TR_HEALTH_MIN_RATIO:-40}"

# A hard floor, used while the peak is still being established. It catches the
# case where the SDR dies during startup, before a healthy peak was ever
# recorded — without it, a container that came up broken would look "fine"
# forever because its peak would be just as low as its current count.
FLOOR="${TR_HEALTH_MIN_THREADS:-20}"

# Survives for the life of the container and is thrown away on restart, which
# is what we want: a fresh start should measure a fresh baseline.
PEAK_FILE="${TR_HEALTH_PEAK_FILE:-/tmp/trunk-recorder-health.peak}"

# Which process to measure. Inside the container Trunk Recorder is PID 1, which
# is why that is the default. Overridable so the script can be pointed at a real
# process from the host when testing it.
PID="${TR_HEALTH_PID:-1}"

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

echo "ok: $threads threads (peak $peak, threshold $threshold)"
exit 0
