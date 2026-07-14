# AGENTS.md — Orientation for an AI coding agent

This file tells another AI agent (Claude, Codex, etc.) what this repository is,
how to run it, and the domain rules you must respect to be functional here.
Read it fully before editing or running anything. Human collaborators can read
it too — it doubles as a README.

---

## 1. What this project is

A **Laser Speckle Contrast Imaging (LSCI)** workspace. It drives a **FLIR
Blackfly S BFS-U3-63S4M** machine-vision camera (Sony IMX178, 3072×2048,
12-bit, USB3) from Python via **Aravis 0.8 + PyGObject (GenICam)**, captures
speckle images, and measures the **speckle contrast**

```
K = σ / ⟨I⟩          (std / mean of intensity in a region)
```

as a function of **camera exposure time**. As exposure grows, a *decorrelating*
sample's speckle blurs and K falls; that fall-off is the **decorrelation curve**,
from which a characteristic decorrelation time τ is fit. The scientific goal is
to characterize sample dynamics (e.g. flow, Brownian motion) from that curve.

This is a research/instrument-control workspace, not a packaged library. Scripts
are run directly and most are configured by **editing a `CONFIG` block at the top
of the file**, not via command-line flags.

---

## 2. Environment — this is the #1 thing that trips agents up

The scientific Python stack (numpy, Pillow, scipy, matplotlib) lives in
**python.org Framework Python** (`/Library/Frameworks/Python.framework/.../python3`),
but **Aravis + PyGObject are installed via Homebrew** under `/opt/homebrew`.
Framework Python cannot find the Homebrew bindings unless three env vars are set
**before the process starts** (macOS `DYLD_*` vars cannot be injected mid-process):

```
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib
PYTHONPATH=/opt/homebrew/lib/pythonX.Y/site-packages
GI_TYPELIB_PATH=/opt/homebrew/lib/girepository-1.0
```

**You do NOT normally need to set these by hand.** `take_exposure_sweep.py` and
`speckle_viewer.py` **self-bootstrap**: on `darwin`, before importing Aravis,
they re-`execve` themselves with those vars set (guarded by `_ARAVIS_BOOTSTRAP=1`
so it happens once). Every camera script that imports `speckle_viewer` inherits
the bootstrap through that import. So the normal invocation is simply:

```bash
python3 speckle_viewer.py
python3 take_exposure_sweep.py
python3 measure_full_curve.py     # imports speckle_viewer -> bootstraps too
```

If you ever add a **new** script that imports Aravis directly (not through
`speckle_viewer`), copy the bootstrap block from the top of `take_exposure_sweep.py`,
or run it with the three env vars exported.

Sanity checks (should all succeed on the configured machine):

```bash
python3 -c "import numpy, PIL, scipy, matplotlib; print('sci stack OK')"
python3 -c "import mytk; print('mytk OK')"
arv-tool-0.8                       # lists connected GenICam cameras
```

**`mytk`** (imported by `speckle_viewer.py`) is the maintainer's own Tk GUI
library, `DCC-Lab/myTk`, installed as a normal package. Only the live viewer
needs it; the headless measurement scripts do not.

---

## 3. Hardware reality — respect these or your data is wrong

The camera has quirks that are baked into the code. Do not "fix" them away.

- **Linear output is mandatory.** Always `GammaEnable=False` (the scripts do
  this). The camera defaults to gamma 0.8 — non-linear, useless for photon-count
  measurements.
- **Gain** is pinned to the **minimum (0 dB)**; there is **no negative gain**.
- **Saturation ceiling is 65408, not 65535.** The 12-bit ADC value is
  left-shifted by 4 into the 16-bit container (4088 × 16 = 65408). Analysis
  thresholds use ~60000–65000 as "clipped". Saturated pixels bias contrast
  **low** — the code drops/flags them.
- **Black-level pedestal ≈ 64 DN** at BlackLevel 0 %. At short exposures the real
  signal can be only a few DN above pedestal — into read-noise. There, the
  *background's own* contrast can masquerade as fast decorrelation. This is a
  real trap; `characterize_background.py` exists to measure it. **Always watch
  the mean signal**, not just K.
- **Link speed matters.** On a USB 2.0 link the full frame is capped ~3 fps. The
  high-speed decorrelation work needs USB 3.0 and uses a **small hardware ROI
  (128×128)** to reach the ~1000 fps firmware ceiling. Below ~128 rows the rate
  is firmware-capped, so 128×128 is the sweet spot.
- **Full-sensor `set_region(0,0,W,H)` can fail** USB3-Vision width/height
  increment rules on this sensor. The capture script deliberately leaves the ROI
  at the power-on default rather than chasing increment math.

---

## 4. Two ways to build a contrast-vs-exposure curve

There are **two independent methods**, and much of the code exists to make them
agree:

1. **Real exposure sweep** (short end, ~8 µs .. ~850 µs): actually change the
   camera exposure and measure K at each. Exact, but long exposures saturate
   and/or exceed the frame period.
2. **Frame summing / synthetic exposure** (long end, ~0.86 ms .. ~1 s): acquire
   thousands of *consecutive* frames at the max-fps operating point, then sum N
   of them to synthesize an N×-longer exposure. Caveat: there is a ~15 % readout
   gap, so "effective exposure" (N × period) and "integrated light" (N × exposure)
   differ — both are reported.

Because vibration rigidly shifts the whole speckle pattern between frames (which
would fake decorrelation when you sum), frames are **registered** by FFT
phase-correlation before summing — see `register.py`.

---

## 5. File map

### Live GUI
- **`speckle_viewer.py`** — real-time `mytk` viewer: live image, ROI tiled
  contrast, rolling contrast plot, background subtraction, hardware-ROI toggle
  for high fps, exposure/gain entry, frame-Δt readout. Defines the reusable
  **`SpeckleCamera`** wrapper and **`roi_stats()`** (tiled contrast) that the
  measurement scripts import. Config block near the top (`CAMERA_ID`,
  `PIXEL_FORMAT`, ROI/exposure defaults, `TILES_PER_AXIS`, etc.).

### Acquisition (talk to the camera)
- **`take_exposure_sweep.py`** — capture a configurable list of exposures,
  `FRAMES_PER_EXPOSURE` frames each, to PNGs in `OUTPUT_DIR`. Stops early on
  saturation. Self-bootstraps. Config block at top.
- **`find_max_fps_exposure.py`** — find the high-speed operating point (highest
  rate just under 1000 fps with the longest exposure that sustains it). Provides
  `longest_exposure_for()` used by the summing tools.
- **`measure_contrast_short_exposure.py`** — the real exposure sweep (short end),
  with photon-matched averaging (frames ∝ 1/exposure).
- **`measure_contrast_vs_exposure.py`** — the frame-summing synthetic-exposure
  method (long end). Registers frames (`ALIGN`) before summing.
- **`measure_full_curve.py`** — does BOTH ends in **one camera session** so the
  illumination is identical and the halves join without stitching. **Preferred**
  entry point for a full curve. Writes both CSVs + one combined PNG.
- **`characterize_background.py`** — **run with the light blocked / lens capped.**
  Measures bias + dark current + fixed-pattern noise vs exposure, so you can tell
  real signal from background contrast.

### Analysis / plotting (no camera needed)
- **`analyze_sweep.py`** — turn a folder of captured PNGs into `contrast.csv`.
  Uses a **fixed pinned ROI** (`FIXED_ROI`) so runs are comparable — contrast is
  region-dependent.
- **`stitch_runs.py`** — stitch a BRIGHT + a DIM PNG sweep (same alignment, only
  attenuation differs) into one clean curve, picking at each exposure the run
  that is neither saturated nor noise-floored. Both runs must share the same ROI.
- **`fit_decorrelation.py`** — fit K(T) with two field-autocorrelation models:
  single-exponential (single scattering / DLS) and DWS sqrt-exp (multiple
  scattering). Reports β, τ, K0.
- **`register.py`** — FFT phase-correlation frame registration (sub-pixel,
  upsampled-DFT). Pure numpy/scipy. `align_stack()`, `shift_rms()`.
- **`plot_full_curve.py`** — overlay the two most-recent short/long CSVs.
- **`compare_decorrelation.py`** — overlay the curves of several saved
  `*_decorrelation_data/` datasets. Compare *shapes*, not absolute K.

### Docs & data
- **`LSCI_THEORY.md`** — the physics: contrast models, decorrelation, fitting.
  Read this to understand *why* the code does what it does.
- **`SESSION_NOTES.md`** — running log of real experiment sessions, settings and
  findings. **Check this first when resuming** — it's the lab notebook.
- **`*_decorrelation_data/`, `paper_*_data/`, `milk_*_data/`** — saved datasets,
  each with CSVs + PNGs + a small `README.md`.
- **`contrast_*.csv`** — analysis outputs from past runs.

---

## 6. Data & git conventions — do not violate

- **Never `git add` captured image data.** `captures/` and `captures_*/` (~GBs of
  PNG frames) are gitignored and must stay untracked. Only **code, docs, and
  small CSVs** are tracked. `.gitignore` already enforces this; keep it that way.
- Past runs live as sibling `captures_<tag>/` folders with a matching
  `contrast_<tag>.csv`. Follow that naming if you add runs.
- CSV formats are stable and read by the plotting/fitting scripts — if you change
  a column, update every consumer.

---

## 7. How to be functional quickly (checklist)

1. Confirm the environment: run the three sanity checks in §2. If Aravis import
   fails, the bootstrap/env is the cause — see §2, not the script logic.
2. To just *look*: `python3 speckle_viewer.py` (needs a camera + display).
3. To *measure a full curve*: check the light is on and stable, then
   `python3 measure_full_curve.py`. Keep illumination fixed for the whole run.
4. To *fit*: `python3 fit_decorrelation.py` (reads the newest CSVs).
5. To *reprocess old PNG captures*: set `CAPTURES`/`FIXED_ROI` in
   `analyze_sweep.py` and run it.
6. Before trusting a short-exposure result, sanity-check against
   `characterize_background.py` (light OFF) — the background can fake decorrelation.
7. When resuming someone else's work, read `SESSION_NOTES.md` and `LSCI_THEORY.md`
   first.

## 8. Conventions when editing

- Configuration is by **editing the `CONFIG` block** at the top of a script, not
  argparse. Match that pattern; don't add a CLI unless asked.
- Reuse `SpeckleCamera` and `roi_stats` from `speckle_viewer.py` rather than
  re-implementing camera setup or contrast — keeping the measurement identical
  across tools is the whole point of the shared helpers.
- Keep contrast **region-consistent**: any comparison across runs must use the
  same ROI. Contrast is not an absolute number; it depends on speckle sampling
  and illumination.
- Preserve the hardware truths in §3 in any new acquisition code (linear output,
  0 dB gain, 65408 ceiling, saturation drops points low).
