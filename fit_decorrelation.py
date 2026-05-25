"""Fit the speckle contrast-vs-exposure curve with two field-autocorrelation
models and compare:

  single-exp (single scattering / DLS):  g1 = exp(-t/tau_c)
      K^2 = beta (e^-2x - 1 + 2x)/(2x^2) + K0^2,  x = T/tau_c
  DWS sqrt-exp (multiple scattering):     g1 = exp(-sqrt(t/tau_s))
      K^2 = 2 beta * INT_0^1 (1-t) exp(-2 sqrt(T t / tau_s)) dt + K0^2

  beta  : T->0 contrast (sampling/coherence limited)
  tau_* : characteristic decorrelation time
  K0    : residual floor (noise + any static fraction)

Reads the latest short + long CSVs (measure_full_curve.py). The short end is
exact real exposures; the long end is frame-summed (~15% gap -> mild systematic
on the time scale). Prints both fits, plots data + both, opens nothing (caller
views the PNG).
"""
import csv, glob
import numpy as np
from scipy.optimize import curve_fit
from scipy.integrate import trapezoid
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

_T = np.linspace(0.0, 1.0, 800)            # fixed quadrature grid for the DWS integral
FIT_MIN_MS = 0.2                           # fit only exposures >= this (short end
                                           # deviates: path-length breadth + shot noise)


def f_single(x):
    x = np.asarray(x, float)
    out = np.empty_like(x)
    small = x < 1e-3
    xs = x[~small]
    out[~small] = (np.exp(-2 * xs) - 1 + 2 * xs) / (2 * xs ** 2)
    out[small] = 1.0 - (2.0 / 3.0) * x[small]
    return out


def model_single(T, beta, tau, K0):
    return np.sqrt(np.clip(beta * f_single(T / tau) + K0 ** 2, 0, None))


def model_dws(T, beta, tau_s, K0):
    T = np.atleast_1d(np.asarray(T, float))
    arg = 2.0 * np.sqrt(np.outer(T, _T) / tau_s)            # (n, grid)
    integ = trapezoid((1 - _T)[None, :] * np.exp(-arg), _T, axis=1)
    return np.sqrt(np.clip(2.0 * beta * integ + K0 ** 2, 0, None))


def load(path, xcol):
    with open(path) as fh:
        rows = list(csv.DictReader(fh))
    return (np.array([float(r[xcol]) for r in rows]),
            np.array([float(r["mean_contrast"]) for r in rows]))


def fit(model, T, K, p0, label):
    bounds = ([0, 1e-4, 0], [1.0, 1e4, 1.0])
    p, cov = curve_fit(model, T, K, p0=p0, bounds=bounds, maxfev=40000)
    err = np.sqrt(np.diag(cov))
    rms = np.sqrt(((K - model(T, *p)) ** 2).mean())
    print(f"[{label}]")
    print(f"    beta  = {p[0]:.4f} +/- {err[0]:.4f}   (K(T->0) = {np.sqrt(p[0]+p[2]**2):.3f})")
    print(f"    tau   = {p[1]*1000:.1f} +/- {err[1]*1000:.1f} us")
    print(f"    K0    = {p[2]:.4f} +/- {err[2]:.4f}")
    print(f"    rms residual = {rms:.4f}\n")
    return p, rms


def main():
    sx, sk = load(sorted(glob.glob("contrast_vs_exposure_short_*.csv"))[-1], "exposure_ms")
    lx, lk = load(sorted(glob.glob("contrast_vs_synthetic_exposure_*.csv"))[-1],
                  "effective_exposure_ms")
    T = np.concatenate([sx, lx])
    K = np.concatenate([sk, lk])
    o = np.argsort(T)
    T, K = T[o], K[o]

    m = T >= FIT_MIN_MS
    print(f"Fitting {int(m.sum())} points with exposure >= {FIT_MIN_MS} ms "
          f"(excluding {int((~m).sum())} shorter)\n")
    Tf, Kf = T[m], K[m]
    p_s, rms_s = fit(model_single, Tf, Kf, [Kf.max()**2, 0.15, Kf.min()], "single-exponential")
    p_d, rms_d = fit(model_dws, Tf, Kf, [Kf.max()**2, 1.0, Kf.min()], "DWS sqrt-exponential")
    print(f"DWS improves rms residual by {rms_s/rms_d:.1f}x "
          f"({rms_s:.4f} -> {rms_d:.4f})")

    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.plot(sx, sk, "o", ms=4, label="short (real exposure)")
    ax.plot(lx, lk, "s", ms=4, label="long (frame summing)")
    Tg = np.geomspace(T.min(), T.max(), 400)
    ax.plot(Tg, model_single(Tg, *p_s), "--", color="gray", lw=1.2,
            label=f"single-exp (τc={p_s[1]*1000:.0f} µs, rms {rms_s:.3f})")
    ax.plot(Tg, model_dws(Tg, *p_d), "-", color="k", lw=1.6,
            label=f"DWS √-exp (τs={p_d[1]*1000:.0f} µs, rms {rms_d:.3f})")
    ax.axvspan(T.min(), FIT_MIN_MS, color="gray", alpha=0.08)
    ax.axvline(FIT_MIN_MS, ls=":", color="red", lw=1,
               label=f"fit ≥ {FIT_MIN_MS} ms (curves extrapolated left)")
    ax.set_xscale("log")
    ax.set_xlabel("exposure  [ms]")
    ax.set_ylabel("speckle contrast  K = σ/⟨I⟩")
    ax.set_title("decorrelation fit: single-exp vs DWS √-exp")
    ax.set_ylim(bottom=0)
    ax.grid(True, which="both", alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig("contrast_fit.png", dpi=120)
    print("Saved contrast_fit.png")


if __name__ == "__main__":
    main()
