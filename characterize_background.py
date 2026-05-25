"""Characterize the camera background vs exposure time (LIGHT OFF).

Hypothesis: at short exposure the real light signal is only a few counts, so the
camera's bias offset (constant) + dark current (proportional to exposure) + its
spatial fixed-pattern noise dominate -- and that background's own contrast
(sigma/mean) can masquerade as a fast "decorrelation" at the short end of the
speckle curve.

** RUN THIS WITH THE LIGHT BLOCKED / LENS CAPPED. **

Sweeps the same 128x128 Mono8 ROI across exposure and reports, per exposure:
  mean      -- DC level: bias offset + dark current
  sptl_std  -- within-frame spatial std (fixed-pattern + noise)
  K_bg      -- tiled roi_stats contrast of the dark frame (directly comparable
               to the speckle K, computed the identical way)
  rd_noise  -- temporal std across frames (read/shot noise)
Fits mean = offset + dark_rate*exposure, and overlays K_bg on the latest speckle
short-exposure curve (if present) so you can see where background takes over.
"""
import csv
import datetime as dt
import glob

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import gi
gi.require_version("Aravis", "0.8")
from gi.repository import Aravis

from speckle_viewer import SpeckleCamera, roi_stats

ROI = 128
M_FRAMES = 150
SAT_LEVEL = 255
EXPOSURES_US = sorted({int(round(x)) for x in np.geomspace(8, 10000, 22)})
DATE = dt.date.today()
OUT_CSV = f"background_vs_exposure_{DATE}.csv"
OUT_PNG = f"background_vs_exposure_{DATE}.png"


def measure_dark_at(cam, exp_us, m):
    cam.set_exposure_us(exp_us)
    applied = cam.get_exposure_us()
    cam.start()
    stream = cam.stream
    for _ in range(5):                                # warm up after exposure change
        b = stream.timeout_pop_buffer(2000000)
        if b:
            stream.push_buffer(b)
    frames = []
    while len(frames) < m:
        b = stream.timeout_pop_buffer(2000000)
        if b is None:
            break
        if b.get_status() == Aravis.BufferStatus.SUCCESS:
            h, w = b.get_image_height(), b.get_image_width()
            frames.append(np.frombuffer(b.get_data(), dtype=np.uint8)
                          .reshape(h, w).copy())
        stream.push_buffer(b)
    cam.stop()
    arr = np.stack(frames).astype(np.float64)
    w = arr.shape[2]
    mean = float(arr.mean())
    sptl_std = float(np.mean([f.std() for f in arr]))
    k_bg = float(np.mean([roi_stats(f, 0, 0, w, SAT_LEVEL)["contrast"] for f in arr]))
    rd_noise = float(arr.std(axis=0).mean())          # temporal std per pixel, averaged
    return applied, mean, sptl_std, k_bg, rd_noise


def main():
    cam = SpeckleCamera(pixel_format="Mono8")
    sw, sh = cam.sensor_size()
    cam.set_roi_region(sw // 2, sh // 2, ROI)
    cam.enable_manual_frame_rate(False)
    print(cam.description())
    print("** LIGHT MUST BE BLOCKED -- this measures the dark background **\n")

    print(f"{'exp_us':>7} {'exp_ms':>8} {'mean':>7} {'sptl_std':>9} "
          f"{'K_bg':>8} {'rd_noise':>9}")
    rows = []
    for e in EXPOSURES_US:
        ap, mean, ss, kbg, rn = measure_dark_at(cam, e, M_FRAMES)
        rows.append((ap, ap / 1000.0, mean, ss, kbg, rn))
        print(f"{ap:7d} {ap / 1000.0:8.3f} {mean:7.2f} {ss:9.3f} "
              f"{kbg:8.4f} {rn:9.3f}")
    if cam.is_running:
        cam.stop()

    exp_us = np.array([r[0] for r in rows], float)
    mean = np.array([r[2] for r in rows], float)
    if mean.max() > 30:
        print("\n  WARNING: mean is high for a dark frame -- is the light really "
              "blocked? These numbers only mean something in the dark.")

    # mean = offset + dark_rate * exposure
    slope, offset = np.polyfit(exp_us, mean, 1)
    print(f"\nBias offset ~ {offset:.2f} counts;  dark current ~ "
          f"{slope * 1000:.4f} counts/ms")

    with open(OUT_CSV, "w", newline="") as fh:
        wri = csv.writer(fh)
        wri.writerow(["exposure_us", "exposure_ms", "mean", "spatial_std",
                      "K_background", "read_noise"])
        wri.writerows(rows)
    print(f"Saved {OUT_CSV}")

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(7, 7), sharex=True)
    ax1.plot(exp_us / 1000.0, mean, "o-", ms=4, label="dark mean")
    ax1.plot(exp_us / 1000.0, offset + slope * exp_us, "--", lw=1,
             label=f"offset {offset:.1f} + {slope*1000:.3f}/ms")
    ax1.set_ylabel("dark mean level  [counts]")
    ax1.set_ylim(bottom=0)
    ax1.legend()
    ax1.grid(True, which="both", alpha=0.3)

    ax2.plot(exp_us / 1000.0, [r[4] for r in rows], "o-", ms=4,
             label="K_background (dark)")
    spk = sorted(glob.glob("contrast_vs_exposure_short_*.csv"))
    if spk:
        with open(spk[-1]) as fh:
            sr = list(csv.DictReader(fh))
        ax2.plot([float(r["exposure_ms"]) for r in sr],
                 [float(r["mean_contrast"]) for r in sr], "s-", ms=4,
                 label="K_speckle (light, short sweep)")
    ax2.set_xscale("log")
    ax2.set_xlabel("exposure  [ms]")
    ax2.set_ylabel("contrast  K = σ/⟨I⟩")
    ax2.set_ylim(bottom=0)
    ax2.legend()
    ax2.grid(True, which="both", alpha=0.3)
    ax1.set_title("camera background vs exposure (dark)")
    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=120)
    print(f"Saved {OUT_PNG}")


if __name__ == "__main__":
    main()
