#!/usr/bin/env bash
#
# Checks that your computer can see the Airspy, and that Docker can use it.
# Run this BEFORE starting the stack:
#
#     ./scripts/check-sdr.sh
#
# Everything here is read-only — it does not change any settings.

set -uo pipefail

ok()   { printf '  \033[0;32mOK\033[0m    %s\n' "$1"; }
bad()  { printf '  \033[0;31mPROBLEM\033[0m %s\n' "$1"; PROBLEMS=$((PROBLEMS+1)); }
note() { printf '        %s\n' "$1"; }

PROBLEMS=0

cd "$(dirname "$0")/.." || exit 1

echo
echo "0. Is your settings file in place?"
if [ -f .env ]; then
  ok "Found .env"
else
  bad "No .env file yet."
  note "Create it by copying the example:"
  note "    cp .env.example .env"
fi

echo
echo "1. Is the Airspy plugged in?"
if ! command -v lsusb >/dev/null 2>&1; then
  note "The 'lsusb' command is not installed, skipping this check."
  note "Install it with:  sudo apt install usbutils"
elif lsusb | grep -qi 'airspy\|1d50:60a1'; then
  ok "Found it:"
  lsusb | grep -i 'airspy\|1d50:60a1' | sed 's/^/        /'
else
  bad "No Airspy found on USB."
  note "Unplug it, plug it back in, and try again. Prefer a USB 3 port"
  note "directly on the computer, not through a hub."
fi

echo
echo "2. Can this account talk to it without sudo?"
if command -v airspy_info >/dev/null 2>&1; then
  if airspy_info >/dev/null 2>&1; then
    ok "Yes."
    airspy_info 2>/dev/null | grep -i 'serial\|firmware' | sed 's/^/        /'
  else
    bad "The Airspy is plugged in but this account cannot open it."
    note "This is a permissions problem. Fix it with the udev rule in"
    note "docs/02-server-setup.md, then unplug and replug the Airspy."
  fi
else
  note "'airspy_info' is not installed, so this check was skipped."
  note "It is optional. To install it:  sudo apt install airspy"
fi

echo
echo "3. Is Docker installed and running?"
if ! command -v docker >/dev/null 2>&1; then
  bad "Docker is not installed. See docs/02-server-setup.md."
elif ! docker info >/dev/null 2>&1; then
  bad "Docker is installed but this account cannot use it."
  note "Usually means you are not in the 'docker' group yet. Run:"
  note "    sudo usermod -aG docker \$USER"
  note "then log out and back in completely."
else
  ok "Docker is running."
fi

echo
echo "4. Can Docker see the GPU?"
if ! command -v nvidia-smi >/dev/null 2>&1; then
  bad "nvidia-smi not found — the NVIDIA driver is not installed."
  note "See docs/02-server-setup.md."
else
  nvidia-smi --query-gpu=name,memory.total,driver_version \
             --format=csv,noheader 2>/dev/null | sed 's/^/        /'
  if docker info 2>/dev/null | grep -qi 'runtimes:.*nvidia'; then
    ok "The NVIDIA container runtime is registered with Docker."
  else
    bad "Docker does not have the NVIDIA runtime."
    note "Install the NVIDIA Container Toolkit — docs/02-server-setup.md."
  fi
fi

echo
if [ "$PROBLEMS" -eq 0 ]; then
  echo "All checks passed. You can start the stack with:  docker compose up -d"
else
  echo "$PROBLEMS problem(s) found above. Fix those first — docs/02-server-setup.md"
  echo "explains each one."
fi
echo
exit 0
