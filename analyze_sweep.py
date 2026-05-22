#!/usr/bin/env python3
"""
Analyze a multi-frame exposure sweep:
  1. Find the brightest 300x300 window in the brightest *non-saturated* frame.
  2. Use that same ROI for every frame.
  3. Group frames by exposure; report the mean contrast (std/mean) averaged
     over the repeats at each exposure, plus the spread (std) across repeats.
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

CAPTURES = Path("captures_2026-05-21_dim")
ROI_SIZE = 300
# Pin the ROI to fixed (top, left) instead of auto-discovering the brightest
# box. Essential for comparing/stitching runs (contrast is region-dependent).
# None = auto-discover per run.
FIXED_ROI = (1010, 1255)
SATURATION = 65000  # 16-bit DN; anything at/above this is treated as clipped
CSV_OUT = CAPTURES / "contrast.csv"


def find_brightest_box(image, size):
    """Return (top, left) of the `size` x `size` window with the largest sum.

    Uses an integral-image trick so every possible window is evaluated in
    one vectorised pass.
    """
    h, w = image.shape
    cs = np.zeros((h + 1, w + 1), dtype=np.float64)
    cs[1:, 1:] = image.astype(np.float64).cumsum(0).cumsum(1)
    box_sums = (cs[size:, size:]
                - cs[:-size, size:]
                - cs[size:, :-size]
                + cs[:-size, :-size])
    top, left = np.unravel_index(int(np.argmax(box_sums)), box_sums.shape)
    return int(top), int(left)


def load_frames():
    frames = []
    for path in sorted(CAPTURES.glob("frame_*.png")):
        m = re.search(r'frame_\d+_0*(\d+)us(?:_rep(\d+))?\.png$', path.name)
        if not m:
            continue
        exp_us = int(m.group(1))
        rep = int(m.group(2)) if m.group(2) is not None else 0
        img = np.array(Image.open(path))
        frames.append((exp_us, rep, path.name, img))
    if not frames:
        sys.exit(f"No frames matched {CAPTURES}/frame_*.png")
    return frames


def main():
    frames = load_frames()

    if FIXED_ROI is not None:
        top, left = FIXED_ROI
        print(f"ROI source         : FIXED_ROI (pinned)")
    else:
        # Pick the brightest non-saturated frame for peak localisation.
        # Saturated frames have a flat plateau, so their argmax is meaningless.
        unsat = [f for f in frames if f[3].max() < SATURATION]
        if not unsat:
            print("Warning: no non-saturated frame found; using the dimmest frame.")
            pick = frames[0]
        else:
            pick = max(unsat, key=lambda f: f[3].max())
        pick_exp, pick_rep, pick_name, pick_img = pick
        top, left = find_brightest_box(pick_img, ROI_SIZE)
        print(f"Peak-finding frame : {pick_name} (max={pick_img.max()})")

    # Group every frame by its exposure so we can average contrast over repeats.
    groups = defaultdict(list)
    for exp_us, rep, name, img in frames:
        groups[exp_us].append(img)

    print(f"Fixed ROI          : rows {top}:{top+ROI_SIZE}, "
          f"cols {left}:{left+ROI_SIZE}  ({ROI_SIZE}x{ROI_SIZE})")
    print(f"Exposures / frames : {len(groups)} exposures, {len(frames)} frames")
    print()
    print(f"{'exposure (us)':>14}  {'n':>3}  {'mean':>10}  "
          f"{'contrast':>10}  {'C std':>9}  {'roi max':>8}  {'sat?':>5}")
    print("-" * 72)

    rows = []
    for exp_us in sorted(groups):
        imgs = groups[exp_us]
        means = []
        contrasts = []
        roi_max = 0
        for img in imgs:
            roi = img[top:top + ROI_SIZE, left:left + ROI_SIZE].astype(np.float64)
            m = roi.mean()
            means.append(m)
            contrasts.append(roi.std() / m if m > 0 else float("nan"))
            roi_max = max(roi_max, int(roi.max()))
        n = len(imgs)
        mean_signal = float(np.mean(means))
        contrast_mean = float(np.mean(contrasts))
        contrast_std = float(np.std(contrasts))
        sat = roi_max >= SATURATION
        print(f"{exp_us:>14d}  {n:>3d}  {mean_signal:>10.2f}  "
              f"{contrast_mean:>10.4f}  {contrast_std:>9.4f}  "
              f"{roi_max:>8d}  {'yes' if sat else 'no':>5}")
        rows.append((exp_us, n, mean_signal, contrast_mean, contrast_std,
                     roi_max, sat))

    with CSV_OUT.open("w") as f:
        f.write("exposure_us,n_frames,mean,contrast_mean,contrast_std,"
                "roi_max,saturated,roi_top,roi_left,roi_size\n")
        for exp_us, n, mean_signal, contrast_mean, contrast_std, roi_max, sat in rows:
            f.write(f"{exp_us},{n},{mean_signal:.6f},{contrast_mean:.6f},"
                    f"{contrast_std:.6f},{roi_max},{int(sat)},"
                    f"{top},{left},{ROI_SIZE}\n")
    print(f"\nWrote {CSV_OUT} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
