#!/usr/bin/env bash
#
# Installs the NVIDIA Container Toolkit — the piece that lets Docker containers
# use your graphics card. Without it, the transcriber cannot reach the GPU.
#
# This does NOT install the graphics driver itself. Install that first:
#     sudo ubuntu-drivers install
#     sudo reboot
# and confirm it worked by running:  nvidia-smi
#
# Usage:   ./scripts/install-nvidia-toolkit.sh
#
# For Ubuntu and Debian. Other distributions: see
# https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html

set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
else
  SUDO="sudo"
fi

echo "==> Checking that the NVIDIA driver is installed"
if ! command -v nvidia-smi >/dev/null 2>&1; then
  cat <<'EOF'

The NVIDIA driver is not installed yet, so there is nothing for the container
toolkit to connect to. Install the driver first:

    sudo ubuntu-drivers install
    sudo reboot

After rebooting, run "nvidia-smi". You should see a table with your GPU in it.
Then run this script again.

EOF
  exit 1
fi
nvidia-smi --query-gpu=name,driver_version --format=csv,noheader

echo
echo "==> Adding NVIDIA's package repository"
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | $SUDO gpg --yes --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | $SUDO tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null

echo
echo "==> Installing"
$SUDO apt-get update
$SUDO apt-get install -y nvidia-container-toolkit

echo
echo "==> Telling Docker about it"
$SUDO nvidia-ctk runtime configure --runtime=docker
$SUDO systemctl restart docker

echo
echo "==> Testing"
if docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi; then
  echo
  echo "Success — Docker can use your GPU."
  echo "Next: docker compose up -d"
else
  echo
  echo "The test failed. The most common causes are:"
  echo "  * you need to log out and back in for docker group membership"
  echo "  * the driver is too old (CUDA 12 needs driver version 525 or newer)"
  exit 1
fi
