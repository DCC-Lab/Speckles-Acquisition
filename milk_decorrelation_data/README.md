# Milk decorrelation data

Speckle contrast vs exposure for a **whole-milk** sample (a dynamic, multiple-
scattering medium), acquired 2026-05-23.

## Acquisition conditions
- Camera: FLIR Blackfly S BFS-U3-16S2M, **Mono16** (12-bit), **128×128** hardware ROI.
- Operating point: ~**994 fps**, **857 µs** exposure, ~149 µs readout gap (~1006 µs period).
- Short end (8–850 µs): real exposure sweep with photon-matched averaging.
- Long end (0.86 ms – 1 s): frame summing (sum N consecutive frames, log-spaced N).
- Produced by `measure_full_curve.py`; fit by `fit_decorrelation.py` (both at repo root).

## Files
- `short_exposure_sweep.csv` — real exposure sweep. Columns: `exposure_us, exposure_ms,
  n_frames, mean_contrast, std_contrast, sem_contrast, mean_level, max_level, sat_frac`.
- `synthetic_exposure_summing.csv` — frame-summing. Columns: `n_frames,
  effective_exposure_ms, integrated_ms, mean_contrast, std_contrast, n_windows`.
- `full_curve.png` — combined contrast-vs-exposure curve, 8 µs – 1 s.
- `dws_fit.png` — single-exp vs DWS √-exp fit.

## Result
- K decays continuously from ~0.24 (8 µs) to ~0.008 (1 s) with **no static plateau**
  → essentially fully dynamic (contrast with paper, which plateaued at a ~0.19 floor).
- Best fit (≥ 0.2 ms): **DWS √-exponential**, g₁ = exp(−γ√(τ/τ_s)), with
  **τ_s ≈ 395 µs**, K₀ ≈ 0.003. Single-exponential fits ~2× worse.
- An additional **faster component below ~0.2 ms** (the measured short end sits above
  the single-τ_s fit) — multiple-scattering path-length breadth and/or a fast mode.
