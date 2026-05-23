"""Synthetic-exposure speckle decorrelation curve.

Acquire a run of *consecutive* frames at the max-fps / longest-exposure
operating point (see find_max_fps_exposure.py), then synthesize longer
exposures by summing N consecutive frames (N = 1, 2, 3, ... up to ~20 ms of
effective exposure) and measure the tiled speckle contrast (the same roi_stats
the viewer uses) for each N. As the synthesized exposure grows, a decorrelating
sample's contrast falls -- that fall-off is the decorrelation curve.

Caveat on "effective exposure": summing N frames spans N x (exposure + gap) of
wall-clock time but integrates light for only N x exposure (the ~149 us gap is
readout dead time, ~15%). So this approximates a continuous exposure at ~85%
duty; both numbers are reported (effective_exposure_ms = N x period, the user's
"N x (exposure + gap)"; integrated_ms = N x exposure, the real light).

Reuses SpeckleCamera and roi_stats; leaves the camera stopped. Saves a CSV.
"""
import csv
import datetime as dt

import numpy as np

import matplotlib
matplotlib.use("Agg")            # headless: render straight to a PNG
import matplotlib.pyplot as plt

import gi
gi.require_version("Aravis", "0.8")
from gi.repository import Aravis

from speckle_viewer import SpeckleCamera, roi_stats
from find_max_fps_exposure import longest_exposure_for

# --- CONFIG ---------------------------------------------------------------
ROI = 128                 # px; the high-fps operating ROI (128x128 hits the ceiling)
TARGET_FPS = 995          # just under the ~998 fps ceiling
MAX_EFFECTIVE_MS = 100.0  # synthesize effective exposures up to this many ms
N_FRAMES = 5000           # consecutive frames to acquire (~5 s at ~1 kHz); enough
                          # that even N~100 sums still average over ~50 windows
SAT_LEVEL = 255           # Mono8 saturation level
OUT_CSV = f"contrast_vs_synthetic_exposure_{dt.date.today()}.csv"
OUT_PNG = f"contrast_vs_synthetic_exposure_{dt.date.today()}.png"


def setup_operating_point(cam):
    """Pin 128x128, the max rate just under 1000 fps, and the longest exposure
    that holds it. Returns (fps, exposure_us, period_us)."""
    sw, sh = cam.sensor_size()
    cam.set_roi_region(sw // 2, sh // 2, ROI)
    cam.set_exposure_us(cam.exposure_bounds_us()[0])      # min exp so rate sets freely
    fps = cam.set_frame_rate(float(TARGET_FPS))
    exp = longest_exposure_for(cam, fps)
    return fps, exp, 1e6 / fps


def acquire_consecutive(cam, n_frames):
    """Grab n_frames in acquisition order with no gaps; return (frames, dropped).

    Pops *every* buffer (FIFO) and recycles it, unlike the viewer's
    latest_frame() which discards all but the newest. dropped>0 means the ring
    starved and the frames are no longer strictly consecutive.
    """
    cam.start()
    stream = cam.stream
    for _ in range(8):                                    # warm up
        b = stream.timeout_pop_buffer(200000)
        if b:
            stream.push_buffer(b)
    c0, f0, u0 = cam.stream_stats()
    frames = []
    while len(frames) < n_frames:
        b = stream.timeout_pop_buffer(500000)
        if b is None:
            cam.stop()
            raise RuntimeError("timed out waiting for a frame")
        if b.get_status() == Aravis.BufferStatus.SUCCESS:
            h, w = b.get_image_height(), b.get_image_width()
            frames.append(np.frombuffer(b.get_data(), dtype=np.uint8)
                          .reshape(h, w).copy())
        stream.push_buffer(b)
    c1, f1, u1 = cam.stream_stats()
    cam.stop()
    return np.stack(frames), (f1 - f0) + (u1 - u0)


def contrast_for_N(frames, N, sat_level=SAT_LEVEL):
    """Mean and std tiled contrast over non-overlapping sums of N frames."""
    size = frames.shape[1]
    n_windows = frames.shape[0] // N
    cs = [roi_stats(frames[w * N:(w + 1) * N].sum(axis=0, dtype=np.float64),
                    0, 0, size, sat_level)["contrast"]
          for w in range(n_windows)]
    cs = np.asarray(cs)
    return float(cs.mean()), float(cs.std()), n_windows


def main():
    cam = SpeckleCamera(pixel_format="Mono8")
    fps, exp, period = setup_operating_point(cam)
    print(cam.description())
    print(f"Operating point: {fps:.1f} fps, exposure {exp} us, "
          f"gap {period - exp:.0f} us, period {period:.0f} us\n")

    print(f"Acquiring {N_FRAMES} consecutive frames (~{N_FRAMES / fps:.1f} s)...")
    frames, dropped = acquire_consecutive(cam, N_FRAMES)
    print(f"  got {len(frames)} frames, dropped {dropped}")
    if dropped:
        print("  WARNING: frames dropped -> summed exposures contain gaps larger "
              "than the readout dead time; the curve is not a clean exposure sweep.")

    sat_frac = float((frames >= SAT_LEVEL).mean())
    print(f"  single-frame level: mean {frames.mean():.1f}, max {frames.max()}, "
          f"saturated {sat_frac * 100:.2f}%")
    if sat_frac > 0.001:
        print("  WARNING: pixels saturate -> summed contrast is biased low.")

    n_max = max(1, min(int(MAX_EFFECTIVE_MS * 1000 / period), len(frames)))
    rows = []
    print(f"\n{'N':>3} {'eff_exp_ms':>11} {'integ_ms':>9} {'contrast':>10} "
          f"{'std':>8} {'windows':>8}")
    for N in range(1, n_max + 1):
        eff_ms = N * period / 1000.0          # N x (exposure + gap)  [user convention]
        integ_ms = N * exp / 1000.0           # N x exposure  [actual integrated light]
        mean_c, std_c, nw = contrast_for_N(frames, N)
        rows.append((N, eff_ms, integ_ms, mean_c, std_c, nw))
        print(f"{N:>3} {eff_ms:11.3f} {integ_ms:9.3f} {mean_c:10.4f} "
              f"{std_c:8.4f} {nw:8d}")

    with open(OUT_CSV, "w", newline="") as fh:
        wri = csv.writer(fh)
        wri.writerow(["n_frames", "effective_exposure_ms", "integrated_ms",
                      "mean_contrast", "std_contrast", "n_windows"])
        wri.writerows(rows)
    print(f"\nSaved {OUT_CSV}")

    # Plot contrast vs effective exposure (y starts at 0 so a dark/noise run --
    # tiny contrast -- is obvious versus real speckle).
    eff = [r[1] for r in rows]
    cont = [r[3] for r in rows]
    err = [r[4] for r in rows]
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.errorbar(eff, cont, yerr=err, fmt="o-", capsize=3, ms=4)
    ax.set_xscale("log")             # exposure spans ~2 decades; log-x shows the decay
    ax.set_xlabel("effective exposure  N×(exposure+gap)  [ms]")
    ax.set_ylabel("speckle contrast  K = σ/⟨I⟩")
    ax.set_title(f"{fps:.0f} fps, {exp} µs exposure, {ROI}×{ROI}  "
                 f"(single-frame ⟨I⟩={frames.mean():.0f})")
    ax.set_ylim(bottom=0)
    ax.grid(True, which="both", alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=120)
    print(f"Saved {OUT_PNG}")

    if cam.is_running:
        cam.stop()


if __name__ == "__main__":
    main()
