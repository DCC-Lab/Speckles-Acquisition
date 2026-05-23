"""Short-exposure end of the speckle contrast curve (8 us .. 850 us).

The frame-summing tool (measure_contrast_vs_exposure.py) builds the LONG end by
summing frames; its shortest point is one full-exposure frame (~857 us). This
script fills the SHORT end the direct way: actually reduce the camera exposure
and measure the tiled contrast at each setting -- a real exposure sweep, no
summing. Together the two span ~8 us to ~100 ms.

Photon-matched averaging: the per-frame signal scales with exposure, so to keep
the contrast estimate's SNR roughly constant across the sweep we average a
number of frames inversely proportional to exposure (frames x exposure = const
= constant total photons collected). The short exposures therefore acquire far
more frames; the standard error of the mean contrast (plotted) stays flat.

Same 128x128 Mono8 ROI as the long-exposure tool so the contrasts are
comparable. Note this improves the *precision* of the average; it does not
raise the per-frame signal -- if the mean is only a few counts (watch the mean
column) any noise bias remains, so for the very short end also brighten the
light or use Mono16.
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

# --- CONFIG ---------------------------------------------------------------
ROI = 128                 # px; same operating ROI as the long-exposure tool
PIXEL_FORMAT = "Mono16"   # 12-bit data in a 16-bit container (~4096 levels);
                          # spans the 8 us .. 1 ms dynamic range that Mono8 can't
# Log-spaced exposures from 8 us to 850 us (min hardware exposure is 4 us).
EXPOSURES_US = sorted({int(round(x)) for x in np.geomspace(8, 850, 18)})
# Photon-matched averaging (see module docstring): frames x exposure ~ const.
M_AT_LONGEST = 200        # frames at the longest exposure (850 us)
M_MAX = 10000             # cap on frames per exposure (~10 s each at ~1 kHz)
OUT_CSV = f"contrast_vs_exposure_short_{dt.date.today()}.csv"
OUT_PNG = f"contrast_vs_exposure_short_{dt.date.today()}.png"


def frames_for(exp_us):
    """Frames to average at exp_us so total photons (frames x exposure) is
    constant -> roughly constant SNR of the contrast estimate. Clamped to
    [M_AT_LONGEST, M_MAX]."""
    longest = max(EXPOSURES_US)
    return int(min(M_MAX, max(M_AT_LONGEST,
                              round(M_AT_LONGEST * longest / exp_us))))


def measure_at(cam, exp_us, m):
    """Acquire m frames at exp_us; return per-exposure contrast + signal stats."""
    cam.set_exposure_us(exp_us)
    applied = cam.get_exposure_us()
    cam.start()
    stream = cam.stream
    for _ in range(5):                                # warm up after exposure change
        b = stream.timeout_pop_buffer(500000)
        if b:
            stream.push_buffer(b)
    dtype = np.uint8 if cam.pixel_format == "Mono8" else np.uint16
    cs, levels, mx, sat = [], [], 0, 0.0
    while len(cs) < m:
        b = stream.timeout_pop_buffer(500000)
        if b is None:
            break
        if b.get_status() == Aravis.BufferStatus.SUCCESS:
            h, w = b.get_image_height(), b.get_image_width()
            fr = np.frombuffer(b.get_data(), dtype=dtype).reshape(h, w)
            s = roi_stats(fr, 0, 0, w, cam.sat_level)
            cs.append(s["contrast"])
            levels.append(s["mean"])
            mx = max(mx, int(fr.max()))
            sat = max(sat, s["sat_frac"])
        stream.push_buffer(b)
    cam.stop()
    cs = np.asarray(cs)
    return applied, float(cs.mean()), float(cs.std()), float(np.mean(levels)), mx, sat


def main():
    cam = SpeckleCamera(pixel_format=PIXEL_FORMAT)
    sw, sh = cam.sensor_size()
    cam.set_roi_region(sw // 2, sh // 2, ROI)
    cam.enable_manual_frame_rate(False)               # free-run: exposure sets freely
    print(cam.description())
    print(f"ROI {ROI}x{ROI}, photon-matched averaging "
          f"({M_AT_LONGEST}..{M_MAX} frames/exposure)\n")

    print(f"{'exp_us':>7} {'exp_ms':>8} {'frames':>7} {'contrast':>10} "
          f"{'sem':>9} {'mean':>7} {'max':>5} {'sat%':>6}")
    rows = []
    for e in EXPOSURES_US:
        m = frames_for(e)
        ap, mc, sc, lvl, mx, sat = measure_at(cam, e, m)
        sem = sc / np.sqrt(m) if m else 0.0
        rows.append((ap, ap / 1000.0, m, mc, sc, sem, lvl, mx, sat))
        flag = ("  <- starved" if lvl < 0.02 * cam.sat_level
                else ("  <- saturating" if sat > 0.001 else ""))
        print(f"{ap:7d} {ap / 1000.0:8.3f} {m:7d} {mc:10.4f} {sem:9.5f} "
              f"{lvl:7.1f} {mx:5d} {sat * 100:6.2f}{flag}")

    with open(OUT_CSV, "w", newline="") as fh:
        wri = csv.writer(fh)
        wri.writerow(["exposure_us", "exposure_ms", "n_frames", "mean_contrast",
                      "std_contrast", "sem_contrast", "mean_level", "max_level",
                      "sat_frac"])
        wri.writerows(rows)
    print(f"\nSaved {OUT_CSV}")

    x = [r[1] for r in rows]
    y = [r[3] for r in rows]
    ye = [r[5] for r in rows]                         # SEM: shrinks as we average more
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.errorbar(x, y, yerr=ye, fmt="o-", capsize=3, ms=4)
    ax.set_xscale("log")
    ax.set_xlabel("exposure  [ms]")
    ax.set_ylabel("speckle contrast  K = σ/⟨I⟩")
    ax.set_title(f"short-exposure sweep  {ROI}×{ROI} {PIXEL_FORMAT} "
                 f"(photon-matched, SEM bars)")
    ax.set_ylim(bottom=0)
    ax.grid(True, which="both", alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=120)
    print(f"Saved {OUT_PNG}")

    if cam.is_running:
        cam.stop()


if __name__ == "__main__":
    main()
