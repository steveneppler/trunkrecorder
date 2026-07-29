# 2. Preparing the computer

This is the longest step. Work through it in order and do not skip the checks —
each one confirms the previous step actually worked, which saves a lot of
confusion later.

Everything here happens in a **terminal**. On Ubuntu Desktop, press
`Ctrl+Alt+T`. On Ubuntu Server you are already looking at one.

> **About `sudo`:** commands starting with `sudo` run as administrator. It will
> ask for your password the first time. Nothing is shown as you type the
> password — no dots, no stars. That is normal. Type it and press Enter.

---

## 2.1 Install Ubuntu

**Ubuntu 24.04 LTS** is recommended. Ubuntu 22.04 also works.

Either the Desktop or Server edition is fine. Desktop is easier if you are new
to this; Server uses fewer resources. If you install Server, choose "Ubuntu
Server (minimized)" only if you are comfortable — the normal option is friendlier.

Once installed, bring it up to date:

```bash
sudo apt update
sudo apt upgrade -y
```

---

## 2.2 Install Docker

Docker is what runs this project's pieces in tidy, self-contained boxes. Do not
install `docker.io` from Ubuntu's own repository — it is old and the
`docker compose` command works differently. Use Docker's official installer:

```bash
curl -fsSL https://get.docker.com | sudo sh
```

Then give your account permission to use Docker without `sudo`:

```bash
sudo usermod -aG docker $USER
```

**Now log out and log back in.** This is not optional and restarting the
terminal is not enough — on Desktop, log out of your session entirely; on
Server, disconnect and reconnect.

Check it worked:

```bash
docker run --rm hello-world
```

You should see "Hello from Docker!". If you instead see "permission denied", you
did not fully log out and back in.

---

## 2.3 Install the NVIDIA graphics driver

This lets the computer use the graphics card at all.

```bash
sudo ubuntu-drivers install
sudo reboot
```

The computer will restart. When it comes back, check:

```bash
nvidia-smi
```

You should get a table showing your card — something like:

```
+-----------------------------------------------------------------------------+
| NVIDIA-SMI 550.90.07    Driver Version: 550.90.07    CUDA Version: 12.4      |
|-------------------------------+----------------------+----------------------+
|   0  NVIDIA GeForce GTX 1070  |   00000000:01:00.0  On |                  N/A |
+-------------------------------+----------------------+----------------------+
```

**The "Driver Version" number must be 525 or higher.** If it is lower, the
transcription software will not run. Update with `sudo ubuntu-drivers install`
and reboot again.

If `nvidia-smi` says "command not found", the driver did not install. Try:

```bash
ubuntu-drivers devices
```

which lists what Ubuntu thinks you have, then install the recommended one
explicitly, e.g. `sudo apt install nvidia-driver-550`.

---

## 2.4 Let Docker use the graphics card

The driver alone is not enough — Docker needs a separate piece called the
**NVIDIA Container Toolkit** to pass the card through to a container.

**This is the step people most often get stuck on.** There is a script for it:

```bash
./scripts/install-nvidia-toolkit.sh
```

It installs the toolkit, tells Docker about it, and then tests it. If it
finishes with "Success — Docker can use your GPU", you are done with this step.

If you would rather do it by hand, it is the procedure from
[NVIDIA's install guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).
Either way, the test at the end is what matters:

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

If that prints the same table you saw earlier, Docker can use the card.

> **What if my card is not NVIDIA, or I have no card at all?**
> The system still works, just slowly. Open `docker-compose.yml`, find the
> `deploy:` block under `transcriber:`, and comment out those six lines by
> putting a `#` at the start of each. Then in `.env` set
> `WHISPER_DEVICE=cpu` and `WHISPER_MODEL=small`.

---

## 2.5 Let the Airspy be used without root

Linux does not, by default, let normal programs talk to arbitrary USB devices.
This adds a rule granting access to the Airspy.

Copy and paste this whole block at once:

```bash
sudo tee /etc/udev/rules.d/52-airspy.rules >/dev/null <<'EOF'
# Airspy R2 / Airspy Mini
SUBSYSTEM=="usb", ATTRS{idVendor}=="1d50", ATTRS{idProduct}=="60a1", MODE="0666", GROUP="plugdev"
EOF

sudo udevadm control --reload-rules
sudo udevadm trigger
```

**Then unplug the Airspy and plug it back in.** The rule only applies to devices
connected after it was loaded.

Optionally, install the Airspy tools so you can test the radio directly:

```bash
sudo apt install -y airspy usbutils
airspy_info
```

That should print your Airspy's serial number and firmware version. If it says
"Cannot open device", the udev rule did not take — re-check the steps above and
make sure you replugged it.

---

## 2.6 Check disk space

Recordings add up quickly — expect **several GB per week** on a busy site.

```bash
df -h .
```

Look at the "Avail" column. You want 100 GB or more. The system deletes
recordings older than 30 days automatically (`RETENTION_DAYS` in `.env`), but it
still needs room for that month.

---

## 2.7 Set the clock

Timestamps on every call come from the computer's clock, so it should be right:

```bash
timedatectl set-timezone America/Denver
timedatectl
```

Confirm "System clock synchronized: yes".

---

## Final check

```bash
./scripts/check-sdr.sh
```

All four checks should pass. If any do not, the output tells you what to fix.

---

**Next:** back to the [README](../README.md) step 3, then
[3. The first run](03-first-run.md)
