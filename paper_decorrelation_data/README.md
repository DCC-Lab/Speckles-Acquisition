# Paper decorrelation data

Speckle contrast vs exposure for a **static paper** sample (the static counterpart
to [milk](../milk_decorrelation_data/)), acquired 2026-05-24. Clean run: light
trimmed to avoid clipping and the setup thermally settled to remove drift.

## Acquisition conditions
- Camera: FLIR Blackfly S BFS-U3-16S2M, **Mono16** (12-bit), **128×128** hardware ROI.
- Operating point: ~**994 fps**, **857 µs** exposure, ~149 µs gap (~1006 µs period).
- Short end (8–850 µs): real exposure sweep, photon-matched averaging.
- Long end (0.86 ms – 1 s): frame summing (log-spaced N).
- Well-exposed, **no clipping** (max ~57k < the 65,408 ceiling), thermally settled.
- Produced by `measure_full_curve.py`.

## Files
- `short_exposure_sweep.csv` — real exposure sweep. Columns: `exposure_us, exposure_ms,
  n_frames, mean_contrast, std_contrast, sem_contrast, mean_level, max_level, sat_frac`.
- `synthetic_exposure_summing.csv` — frame-summing. Columns: `n_frames,
  effective_exposure_ms, integrated_ms, mean_contrast, std_contrast, n_windows`.
- `full_curve.png` — combined contrast-vs-exposure curve, 8 µs – 1 s.

## Result
- Contrast is **flat at ~0.26 across the entire 8 µs – 1 s range**
  (K ≈ 0.282 at 8 µs → 0.263 at 1 ms → 0.255 at 1 s) — the hallmark of a **static**
  sample: the speckle does not decorrelate, so no exposure averages it down.
  This ~0.26 is the static-speckle floor set by the optical sampling.
- The slight elevation at the very shortest exposures (≤ ~20 µs) is minor residual
  shot noise (those points are the dimmest, mean ~470–700); the ~3% downslope over
  1 ms–1 s is negligible residual drift.
- Contrast with the dynamic milk sample, whose K decays continuously to ~0.008.

### Note on earlier attempts
The first paper run drifted at long exposure; a later run saturated; another was
over-dimmed (short-end shot noise). This run trimmed the light to avoid clipping
and let the setup settle, fixing all three — hence the flat curve.
