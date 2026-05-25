"""Overlay the contrast-vs-exposure curves of all saved *_decorrelation_data
datasets on one log-x axis for comparison.

Each dataset directory holds short_exposure_sweep.csv (real exposures) and
synthetic_exposure_summing.csv (frame summing); they are concatenated into one
curve per sample. NOTE: absolute K depends on the speckle sampling/illumination
of each acquisition, so compare *shapes* (does K plateau or fall to zero?) more
than absolute levels across samples taken under different conditions.
"""
import csv, glob, os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def load_dataset(d):
    def rows(name):
        with open(os.path.join(d, name)) as f:
            return list(csv.DictReader(f))
    s = rows("short_exposure_sweep.csv")
    l = rows("synthetic_exposure_summing.csv")
    T = np.array([float(r["exposure_ms"]) for r in s]
                 + [float(r["effective_exposure_ms"]) for r in l])
    K = np.array([float(r["mean_contrast"]) for r in s]
                 + [float(r["mean_contrast"]) for r in l])
    o = np.argsort(T)
    return T[o], K[o]


def main():
    dirs = sorted(glob.glob("*_decorrelation_data"))
    if not dirs:
        raise SystemExit("no *_decorrelation_data directories found")
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(7.5, 8), sharex=True)
    for d in dirs:
        T, K = load_dataset(d)
        label = d.replace("_decorrelation_data", "")
        ax1.plot(T, K, "o-", ms=3, label=label)
        ax2.plot(T, K / K[0], "o-", ms=3, label=label)   # K[0] = shortest-exposure ~ beta
    ax1.set_ylabel("speckle contrast  K = σ/⟨I⟩")
    ax1.set_title("raw — absolute K depends on optical sampling per acquisition")
    ax2.set_ylabel("K / K(shortest)  (β-normalized)")
    ax2.set_title("normalized — optical offset cancels, decorrelation shape only")
    ax2.set_xlabel("exposure  [ms]")
    for ax in (ax1, ax2):
        ax.set_xscale("log")
        ax.set_ylim(bottom=0)
        ax.grid(True, which="both", alpha=0.3)
        ax.legend()
    fig.suptitle("speckle contrast vs exposure — sample comparison")
    fig.tight_layout()
    fig.savefig("compare_decorrelation.png", dpi=120)
    print("Saved compare_decorrelation.png from:", ", ".join(dirs))


if __name__ == "__main__":
    main()
