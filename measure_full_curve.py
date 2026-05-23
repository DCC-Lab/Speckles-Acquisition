"""Acquire the FULL speckle contrast-vs-exposure curve in ONE camera session,
so both halves share the same illumination and join without stitching:

  short end (~8 us .. 850 us)   -- real exposure sweep, photon-matched averaging
  long  end (~0.86 ms .. 100 ms) -- frame summing at the max-fps operating point

Reuses the building blocks of the two standalone tools (measure_contrast_short_
exposure.py and measure_contrast_vs_exposure.py) so it stays in sync with them.
Writes both CSVs (standard formats) and one combined log-x PNG.

** Keep the illumination fixed for the whole run -- that is the entire point. **
"""
import csv
import datetime as dt

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from speckle_viewer import SpeckleCamera
import measure_contrast_short_exposure as shortm
import measure_contrast_vs_exposure as longm

DATE = dt.date.today()
SHORT_CSV = f"contrast_vs_exposure_short_{DATE}.csv"
LONG_CSV = f"contrast_vs_synthetic_exposure_{DATE}.csv"
OUT_PNG = f"contrast_full_curve_{DATE}.png"


def run_short(cam):
    """Real exposure sweep, photon-matched. Returns list of result dicts."""
    cam.enable_manual_frame_rate(False)               # free-run; exposure sets freely
    print("Short end -- real exposure sweep (photon-matched):")
    print(f"{'exp_us':>7} {'frames':>7} {'contrast':>10} {'sem':>9} {'mean':>7}")
    rows = []
    for e in shortm.EXPOSURES_US:
        m = shortm.frames_for(e)
        ap, mc, sc, lvl, mx, sat = shortm.measure_at(cam, e, m)
        sem = sc / np.sqrt(m) if m else 0.0
        rows.append(dict(exp_us=ap, exp_ms=ap / 1000.0, frames=m, K=mc,
                         std=sc, sem=sem, mean=lvl, mx=mx, sat=sat))
        flag = "  <- starved" if lvl < 5 else ("  <- sat" if sat > 0.001 else "")
        print(f"{ap:7d} {m:7d} {mc:10.4f} {sem:9.5f} {lvl:7.1f}{flag}")
    return rows


def run_long(cam):
    """Frame-summing synthetic exposures at the operating point. Returns rows."""
    fps, exp, period = longm.setup_operating_point(cam)
    print(f"\nLong end -- frame summing at {fps:.0f} fps, {exp} us exposure, "
          f"period {period:.0f} us")
    frames, dropped = longm.acquire_consecutive(cam, longm.N_FRAMES)
    print(f"  {len(frames)} frames, dropped {dropped}, "
          f"mean {frames.mean():.1f}, max {frames.max()}")
    n_max = max(1, min(int(longm.MAX_EFFECTIVE_MS * 1000 / period), len(frames)))
    rows = []
    for N in range(1, n_max + 1):
        mc, sc, nw = longm.contrast_for_N(frames, N)
        rows.append(dict(N=N, eff_ms=N * period / 1000.0, integ_ms=N * exp / 1000.0,
                         K=mc, std=sc, sem=sc / np.sqrt(nw) if nw else 0.0, nwin=nw))
    return rows, frames, dropped


def main():
    cam = SpeckleCamera(pixel_format="Mono8")
    sw, sh = cam.sensor_size()
    cam.set_roi_region(sw // 2, sh // 2, shortm.ROI)
    print(cam.description())
    print("** keep illumination fixed for the whole run **\n")

    short_rows = run_short(cam)
    long_rows, frames, dropped = run_long(cam)
    if cam.is_running:
        cam.stop()

    # Sanity: the two halves overlap at ~0.85 ms; report the gap at the join.
    join_short = short_rows[-1]["K"]
    join_long = long_rows[0]["K"]
    print(f"\nJoin at ~0.85 ms: short {join_short:.4f} vs long {join_long:.4f} "
          f"(Δ {abs(join_short - join_long):.4f})")
    if dropped:
        print("  WARNING: long-end frames dropped -> summed exposures have gaps.")

    with open(SHORT_CSV, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["exposure_us", "exposure_ms", "n_frames", "mean_contrast",
                    "std_contrast", "sem_contrast", "mean_level", "max_level",
                    "sat_frac"])
        for r in short_rows:
            w.writerow([r["exp_us"], r["exp_ms"], r["frames"], r["K"], r["std"],
                        r["sem"], r["mean"], r["mx"], r["sat"]])
    with open(LONG_CSV, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["n_frames", "effective_exposure_ms", "integrated_ms",
                    "mean_contrast", "std_contrast", "n_windows"])
        for r in long_rows:
            w.writerow([r["N"], r["eff_ms"], r["integ_ms"], r["K"], r["std"], r["nwin"]])
    print(f"Saved {SHORT_CSV} and {LONG_CSV}")

    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.errorbar([r["exp_ms"] for r in short_rows], [r["K"] for r in short_rows],
                yerr=[r["sem"] for r in short_rows], fmt="o-", ms=4, capsize=3,
                label="short end: real exposure sweep")
    ax.errorbar([r["eff_ms"] for r in long_rows], [r["K"] for r in long_rows],
                yerr=[r["sem"] for r in long_rows], fmt="s-", ms=4, capsize=3,
                label="long end: frame summing")
    ax.set_xscale("log")
    ax.set_xlabel("exposure  [ms]")
    ax.set_ylabel("speckle contrast  K = σ/⟨I⟩")
    ax.set_title(f"full speckle contrast vs exposure  {shortm.ROI}×{shortm.ROI} "
                 f"Mono8 (one session)")
    ax.set_ylim(bottom=0)
    ax.grid(True, which="both", alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=120)
    print(f"Saved {OUT_PNG}")


if __name__ == "__main__":
    main()
