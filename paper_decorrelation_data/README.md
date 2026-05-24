# Paper decorrelation data

Speckle contrast vs exposure for a **static paper** sample (a rigid, non-moving
scatterer — the static counterpart to [milk](../milk_decorrelation_data/)),
acquired 2026-05-24.

## Acquisition conditions
- Camera: FLIR Blackfly S BFS-U3-16S2M, **Mono16** (12-bit), **128×128** hardware ROI.
- Operating point: ~**994 fps**, **857 µs** exposure, ~149 µs gap (~1006 µs period).
- Short end (8–850 µs): real exposure sweep, photon-matched averaging.
- Long end (0.86 ms – 1 s): frame summing (log-spaced N).
- Illumination reduced ~3× from the milk run (paper reflects much more). A few
  bright pixels still graze the 65,408 ceiling but the saturated fraction is
  < 0.1% (negligible for the tiled contrast).
- Produced by `measure_full_curve.py`.

## Files
- `short_exposure_sweep.csv` — real exposure sweep. Columns: `exposure_us, exposure_ms,
  n_frames, mean_contrast, std_contrast, sem_contrast, mean_level, max_level, sat_frac`.
- `synthetic_exposure_summing.csv` — frame-summing. Columns: `n_frames,
  effective_exposure_ms, integrated_ms, mean_contrast, std_contrast, n_windows`.
- `full_curve.png` — combined contrast-vs-exposure curve, 8 µs – 1 s.

## Result
- Fast decay from K ≈ 0.17 (8 µs) to a **quasi-static plateau ~0.127** over ~0.1–10 ms
  — the static-speckle floor expected for a non-moving sample.
- **Caveat:** beyond ~10 ms the contrast declines (~0.127 → 0.09 at 1 s). Paper is
  static, so this slow tail is most likely **setup drift** (vibration/thermal/air
  over the multi-second acquisition), *not* intrinsic paper dynamics. For a flatter
  static reference use more rigid mounting / a shorter acquisition.
- Overall contrast (~0.13) is lower than an earlier paper run (~0.19–0.28); the
  mounting/aperture (speckle sampling) changed when the sample was swapped.
