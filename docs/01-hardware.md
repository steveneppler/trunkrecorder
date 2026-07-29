# 1. The hardware

Read this before buying anything. The antenna matters more than the computer,
the radio, or anything you can configure in software.

---

## Why the antenna comes first

The Colorado DTRS site you are receiving transmits around **852 MHz**. That is a
short wavelength — signals at this frequency travel in more or less straight
lines and are blocked by terrain, buildings, and even foliage.

Grand Junction sits in a valley ringed by high ground. Whether you can hear the
site is mostly a question of whether your antenna can see it. No amount of
configuration fixes an antenna in a basement.

If a call sounds like robotic garbling, that is almost always signal quality,
not a setting.

---

## What to buy

### Antenna

You want something tuned for the **800 MHz** band. Any of these work:

- A **purpose-built 800 MHz scanner antenna** — the best result for the money.
  Look for one specified for 806–870 MHz.
- A **discone** antenna — receives everything, less well than a tuned antenna,
  but useful if you want to listen to other things too.
- A **home-made quarter-wave ground plane** — a vertical element about
  **3.4 inches (8.7 cm)** long with four radials of the same length. People get
  good results from these; the dimensions are what matter.

A "scanner antenna" from a big-box store that claims 25–1300 MHz will work
poorly at 852 MHz. It is a reasonable thing to start with if you already own
one, but do not buy one.

### Where to put it

In order of preference:

1. **Outside, on the roof**, with a clear view toward the transmitter site
2. Outside, on a mast on the side of the house, above the roofline
3. In an attic (works, but loses signal to the roof material)
4. Near a window facing the right direction
5. Sitting on the desk next to the computer — expect this to disappoint you

Higher is better. Every foot of height helps more than any setting in this
software.

### Coax cable

The cable between the antenna and the radio loses signal, and at 852 MHz it
loses a lot.

- Use **LMR-400** or **RG-6** — not RG-58, and not the thin cable that comes
  coiled up with a cheap antenna.
- Keep it **as short as you can**. A 100-foot run of thin coax can throw away
  most of what your antenna picked up.
- The Airspy has an **SMA** connector, so you will likely need an adapter
  (commonly SO-239/PL-259 or F-type to SMA).

### The radio: Airspy R2 or Airspy Mini

Either works. The difference that matters here:

| | Airspy R2 | Airspy Mini |
| --- | --- | --- |
| Bandwidth it can watch at once | 10 MHz | 6 MHz |
| Load on your CPU | higher | lower |
| Price | more | less |

The Grand Junction site's channels all sit between **851 and 854 MHz** — a 3 MHz
span — so **both are comfortably wide enough**. The Mini is the easier choice
if your CPU is modest.

If you use a **Mini**, change one line in `config/trunk-recorder/config.json`:

```json
"rate": 6000000,
```

(The default is `10000000`, which is right for the R2.)

### Optional but worth it

- A **band-pass filter** for 800 MHz. Nearby FM broadcast and cellular
  transmitters can overload the Airspy's front end, which makes everything
  sound worse in a way that is hard to diagnose. If you live near a broadcast
  tower, this can be the difference between working and not.
- **Lightning protection** if the antenna goes outside. This is a safety issue,
  not a performance one.

---

## Plugging it in

1. Attach the antenna to the Airspy with the coax.
2. Plug the Airspy into a **USB 3 port directly on the computer**. Not a hub —
   the Airspy moves a lot of data and USB hubs cause dropouts that look like
   poor reception.
3. Keep the Airspy away from the computer's case and any USB 3 hard drives if
   you can. USB 3 is electrically noisy at these frequencies.

Then confirm the computer can see it:

```bash
./scripts/check-sdr.sh
```

---

**Next:** [2. Preparing the computer](02-server-setup.md)
