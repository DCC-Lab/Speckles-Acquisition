"""Overlay the two halves of the speckle contrast-vs-exposure curve on one
log-x axis (~8 us .. 100 ms):

  short end  -- real exposure sweep   (measure_contrast_short_exposure.py)
  long end   -- frame summing         (measure_contrast_vs_exposure.py)

Reads the most recent CSV from each tool; no camera needed. Error bars are SEM
for both. NOTE: the two halves are separate acquisitions -- if the illumination
differed between them, the curves may not line up exactly at the ~0.85 ms join.
"""
import csv
import datetime as dt
import glob

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def latest(pattern):
    files = sorted(glob.glob(pattern))
    if not files:
        raise SystemExit(f"no file matching {pattern!r} -- run that measurement first")
    return files[-1]


def read_csv(path):
    with open(path) as fh:
        return list(csv.DictReader(fh))


def main():
    short = read_csv(latest("contrast_vs_exposure_short_*.csv"))
    long_ = read_csv(latest("contrast_vs_synthetic_exposure_*.csv"))

    sx = [float(r["exposure_ms"]) for r in short]
    sy = [float(r["mean_contrast"]) for r in short]
    se = [float(r["sem_contrast"]) for r in short]

    lx = [float(r["effective_exposure_ms"]) for r in long_]
    ly = [float(r["mean_contrast"]) for r in long_]
    le = [float(r["std_contrast"]) / np.sqrt(float(r["n_windows"])) for r in long_]

    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.errorbar(sx, sy, yerr=se, fmt="o-", ms=4, capsize=3,
                label="short end: real exposure sweep")
    ax.errorbar(lx, ly, yerr=le, fmt="s-", ms=4, capsize=3,
                label="long end: frame summing")
    ax.set_xscale("log")
    ax.set_xlabel("exposure  [ms]")
    ax.set_ylabel("speckle contrast  K = σ/⟨I⟩")
    ax.set_title("full speckle contrast vs exposure  (128×128 Mono8)")
    ax.set_ylim(bottom=0)
    ax.grid(True, which="both", alpha=0.3)
    ax.legend()
    out = f"contrast_full_curve_{dt.date.today()}.png"
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
