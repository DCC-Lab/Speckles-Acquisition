# Paper + medium spot

Speckle contrast **short-exposure sweep** for static paper with a **medium
illumination spot**, acquired 2026-05-24. Lensless (free-space) geometry, where
spot size sets the speckle grain size (s ~ 1.22 λL/D).

## Acquisition conditions
- FLIR Blackfly S BFS-U3-16S2M, Mono16, 128x128 ROI, ~994 fps / 857 us.
- Short end (8-850 us): real exposure sweep, photon-matched averaging.
- **Medium spot**: between the over-large spot (undersampled, K ~ 0.13-0.26) and
  the too-small spot (well-sampled but too few speckles in the ROI -> jittery K).

## Files
- `short_exposure_sweep.csv` -- the sweep (columns: exposure_us, exposure_ms,
  n_frames, mean_contrast, std_contrast, sem_contrast, mean_level, max_level, sat_frac).
- `short_sweep.png` -- the figure.

## Result
- Contrast **flat at ~0.31** (0.323 at 8 us -> 0.307 at 850 us), **smooth**
  (SEM ~2e-5) -- the medium spot is the sweet spot: high, well-sampled contrast
  without the jitter of the too-small spot.

## Note -- long end not acquired (thermal)
The frame-summing long end (1 ms - 1 s) could not be taken: after a long session
the camera was **thermally throttling** (sensor 64 C), which stalls sustained
streaming at ~3 s (survives usbfs/USB-reset/replug -- it's heat, not the bus).
Let the camera cool (~10-15 min, powered off), then re-run `measure_full_curve.py`
for the long end. For static paper it's expected flat at this contrast
(cf. ../paper_decorrelation_data).
