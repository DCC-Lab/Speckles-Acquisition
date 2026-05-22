# FLIR / Aravis exposure-sweep session notes

## What's here

Two scripts:

- **`take_exposure_sweep.py`** — captures images per exposure into `OUTPUT_DIR`.
  Edit the CONFIG block at the top:
  - `EXPOSURE_TIMES_US` — list of exposures (µs). Currently a decade-stepped
    sweep from 10 µs to 100 ms (37 points).
  - `FRAMES_PER_EXPOSURE` — frames to grab at each exposure. `1` = single shot
    (`frame_NN_<exp>us.png`); `>1` saves `frame_NN_<exp>us_repRR.png` for
    averaging in analysis.
  - `OUTPUT_DIR` — where the PNGs go; set per run (e.g. a dated directory).
  - `PIXEL_FORMAT = "Mono16"` (linear 16-bit; camera is 12-bit shifted into 16).
  - `SATURATION_STOP = 60000` — sweep auto-stops when peak ≥ this so you can
    re-attenuate. Set higher to push closer to full saturation.
  - Camera already configured for: gain pinned to 0 dB, `GammaEnable=False`,
    auto-exposure/gain off, continuous acquisition mode.
- **`analyze_sweep.py`** — re-reads `CAPTURES`, finds the brightest 300×300
  window in the brightest non-saturated frame, then uses that same ROI on every
  frame. Groups frames by exposure and reports `contrast_mean` ± `contrast_std`
  (= std/mean averaged over the repeats, plus the spread across them). Writes
  `contrast.csv` into the `CAPTURES` dir. Filename regex accepts both the
  single-shot and `_repRR` naming. Set `CAPTURES` to match the sweep's
  `OUTPUT_DIR`.

Both scripts self-bootstrap the Homebrew env vars on macOS (DYLD/PYTHONPATH/
GI_TYPELIB_PATH) so you can just run `python3 take_exposure_sweep.py` from the
python.org Framework Python.

Companion document: **`LSCI_THEORY.md`** — physics framework for interpreting
contrast curves, β calibration, laser-coherence corrections, single-exposure
inversion, and the τ_sample → viscosity link.

## How to run

```
python3 /Users/dccote/GitHub/flir/take_exposure_sweep.py
python3 /Users/dccote/GitHub/flir/analyze_sweep.py
pbcopy < /Users/dccote/GitHub/flir/contrast.csv      # CSV onto clipboard
```

## Current state (2026-05-21 session)

Re-attenuation + multi-frame averaging. At the start of the day the system was
~3000× too bright (10 µs already pinned at the 65408 ceiling); that first
aborted attempt clobbered `frame_00` in `captures/`, so **`captures/` is now a
stale mix — do not analyze it.** After attenuating:

- **`captures_2026-05-21/`** — single-frame sweep, 29 frames, 10 µs → 20 ms
  (auto-stop at 20 ms, peak 62144). `contrast.csv` inside the dir.
  ROI rows 1390:1690, cols 880:1180.
  **Contrast low and flat-ish: ~0.18 (10 µs) drifting to ~0.14 (20 ms).**
  Mean ~513 DN at 10 µs.
- **`captures_2026-05-21_multi/`** — multi-frame sweep, `FRAMES_PER_EXPOSURE=10`,
  300 frames over 30 exposures, 10 µs → 30 ms (auto-stop at 30 ms, ceiling
  65408). Source had drifted ~25 % dimmer than the single-frame run, so it got
  one extra exposure step before clipping. Averaged `contrast.csv` inside the
  dir. ROI rows 1352:1652, cols 885:1185.
  - **Reproducibility is excellent**: contrast std across the 10 repeats is
    ~0.0005–0.002 (0.1–0.3 %) at almost every exposure. A handful of outlier
    exposures (50 µs, 800 µs, 900 µs, 2 ms) have C std 0.014–0.032 — single
    glitch frames worth inspecting.
  - **Proper LSCI-shaped curve this time**: C rises to **β ≈ 0.66** by
    100–200 µs, then rolls off to a **~0.49 plateau** by 3–30 ms. The dip below
    0.66 at 10–40 µs is the 64-DN pedestal diluting std/mean (mean only ~113 DN
    at 10 µs), now cleanly resolved by the small error bars.
- **`captures_2026-05-21_highpower/`** — multi-frame sweep (10/exposure) after
  raising laser power ~13× (10 µs peak 12032 vs 896 in the prior multi run).
  200 frames, 20 exposures, 10 µs → 2 ms (auto-stop at 2 ms, ceiling 65408 —
  saturates far earlier at this power). ROI rows 1010:1310, cols 1255:1555
  (moved again). **Flat plateau C ≈ 0.50** across the whole 10 µs–2 ms band,
  C std ~0.0005. The short-T pedestal dip is gone (10 µs mean now 1842 DN),
  confirming that dip was purely the 64-DN black-level pedestal.
- **Run-to-run β differences explained — the laser was realigned during the
  session** (per operator). Realignment changes focus → speckle-grain size → β,
  and moves which region is imaged, so the contrast plateau is *expected* to
  shift between runs: single-frame ~0.15, multi ~0.66 → 0.49, high-power ~0.50.
  These are **not comparable across a realignment** — each is the β of its own
  alignment. Pinning the ROI only helps *within* one fixed alignment; across a
  realignment β legitimately changes, so re-measure β after every alignment.

## Prior run — paper sample (static-β reference)

- Sample changed from the original speckle target to **a piece of paper**.
- `captures/` — 30 frames captured (10 µs → 30 ms) before auto-stop on
  saturation at frame 29 (30 ms, max = 65 408 = 12-bit ceiling).
- `contrast.csv` — corresponding analysis, ROI rows 1269:1569, cols 916:1216
  (300×300).
- **Contrast is essentially flat at C ≈ 0.49–0.52 from 30 µs out to 30 ms.**
  Short-T points (10 µs, 20 µs) are inflated to ~0.61–0.63 by read-noise
  pedestal as usual.
- Interpretation: paper is a static surface scatterer (τ_sample → ∞), so the
  flat plateau **is** β for the current optical setup with this laser. No
  rolloff visible across the 30 µs–30 ms band means the laser's effective
  τ_laser is well outside that window (HeNe — ns-scale mode bandwidth averaged
  fully into β; see `LSCI_THEORY.md`).
- Last sweep before this — same setup, original dynamic-speckle sample —
  archived as `captures_prev_sample/` + `contrast_prev_sample.csv` (37 frames,
  10 µs → 100 ms, contrast 0.44 → 0.044, the expected LSCI rolloff).

## Backed-up earlier runs

In rough chronological order. Each is `captures_<tag>/` + `contrast_<tag>.csv`.

| tag | what it is |
|---|---|
| `run1` | first 10-point sweep, base attenuation |
| `run2` | same 10-point sweep, laser ~4× brighter — used to confirm contrast is intensity-independent at fixed ROI/exposure |
| `to10ms` | first fine sweep (28 points, 10 µs → 10 ms, base attenuation) |
| `to100ms_sat` | 37-point sweep, attempted 10 µs → 100 ms at base attenuation — saturated from 10 ms onward |
| `pre_atten` | early-stop run after adding `SATURATION_STOP`, halted at 9 ms (peak 61 120) |
| `too_dim` | first re-attenuation try — over-attenuated by ~15×, signal in noise floor |
| `prev_sample` | clean 10 µs → 100 ms sweep on the previous (dynamic) speckle sample, C: 0.44 → 0.044 |
| `captures/` (paper) | paper-sample run, saturates at 30 ms; `frame_00` clobbered 2026-05-21 — now stale |
| `2026-05-21` | re-attenuated single-frame sweep, 10 µs → 20 ms, flat C ≈ 0.14–0.18 |
| `2026-05-21_multi` | 10-frames/exposure sweep, 10 µs → 30 ms, β ≈ 0.66 → 0.49 plateau, C std ~0.001 |
| `2026-05-21_highpower` | 10/exposure, power ~13× up, 10 µs → 2 ms (sat at 2 ms), flat C ≈ 0.50; after laser realignment |

## Key findings

- **Contrast is intensity-independent** when ROI and exposure match across runs
  (run1 vs run2 agreed within ±0.01 over a 4× intensity change).
- **Contrast decreases with exposure for dynamic samples** (prev_sample run:
  0.44 → 0.044 over 10 µs → 100 ms): classic time-integrated-speckle signature.
  This is the LSCI signal.
- **Contrast is flat for static samples** (paper, current run: ~0.50 plateau).
  The plateau value is the β of the optical+laser system. Useful as a
  reference: changes to objective, magnification, polarization, ND filters,
  or laser will all shift β and must be re-measured.
- **Dynamic-range squeeze**: a single attenuation can't give clean data across
  10 µs → 100 ms on a single sample. Standard fix is two sweeps (bright + dim)
  with overlap in the 1–5 ms region, stitched together. Not yet implemented.
- **Frame-to-frame contrast is highly reproducible** (`2026-05-21_multi`, 10
  repeats/exposure): contrast std across repeats is ~0.1–0.3 % at fixed
  exposure/ROI. Averaging cleanly separates the pedestal-suppressed short-T
  points from the real rolloff; isolated outlier frames stand out as C-std spikes.
- **Read-noise inflation** at the shortest exposures (10–100 µs) when mean
  signal is only ~20–80 DN above the 64 DN black-level pedestal. Affects both
  dynamic and static samples. The 1 ms → 100 ms portion is the trustworthy one
  for fits.

## Camera particulars (BFS-U3-63S4M, IMX178, USB3)

- Exposure bounds: 8 µs .. 30 s.
- Gain bounds: 0 .. 47.99 dB. **No negative gain available.**
- BlackLevel can go to −5 % but won't help signal headroom (just clips toe).
- `DigitalShift`: not implemented on this model.
- 16-bit container holds 12-bit data left-shifted by 4 → saturation ceiling
  is 65408 (= 4088 × 16), not 65535.
- Black-level pedestal at BlackLevel=0%: 64 DN in 16-bit.

## macOS env-bootstrap (for reference)

The python.org Framework Python at
`/Library/Frameworks/Python.framework/Versions/3.14/bin/python3` can't see
Aravis/PyGObject from Homebrew without:

```
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib
PYTHONPATH=/opt/homebrew/lib/python3.14/site-packages
GI_TYPELIB_PATH=/opt/homebrew/lib/girepository-1.0
```

`take_exposure_sweep.py` re-execs itself with these set so you don't need to
think about it.

## Possible next steps

- **Re-do paper sweep with ~3× more ND** to avoid 30 ms saturation, and
  extend `EXPOSURE_TIMES_US` to 200 ms, 500 ms, 1 s, 5 s. Goal: bracket the
  HeNe's τ_laser from above (if the plateau extends out to seconds, τ_laser
  is effectively infinite for any LSCI experiment).
- **Swap to the 650 nm diode and rerun the paper sweep** with identical
  optical path. Any droop in C(T) is the diode's added decorrelation channel
  (mode hopping). Fit τ_laser_diode for use in the per-frame deconvolution.
- **Add `invert_contrast.py`** — single-exposure C → τ_apparent inverter,
  vectorised via LUT. Takes β and τ_laser as args; outputs τ_apparent and
  τ_sample columns. Useful for the milk-coagulation time-series work.
- Stitch a bright + dim sweep on the dynamic sample to get clean data across
  the full 10 µs → 100 ms range.
- Fit an LSCI decorrelation model
  `C(T) = β·sqrt(τ/(2T)·(1−exp(−2T/τ)) + ε²)` to extract τ.
- ~~Take N repeated frames at a fixed exposure~~ — DONE via
  `FRAMES_PER_EXPOSURE` (2026-05-21); analysis averages contrast and reports the
  spread. Still TODO: use the repeats to actually *decompose* temporal
  (shot/read) vs spatial (speckle + illumination) variance, not just average.
- Pin the ROI as a constant (currently re-discovered per run) in
  `analyze_sweep.py` so it doesn't drift between samples.
