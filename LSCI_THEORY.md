# LSCI theory notes for this workspace

Physics framework underlying the exposure-sweep and milk-coagulation
experiments. Companion to `SESSION_NOTES.md`.

## 1. The LSCI contrast model

For a partially decorrelated speckle pattern integrated over exposure `T`:

```
C(T) = β · sqrt[ τ/(2T) · (1 − exp(−2T/τ)) ] + ε
```

where:
- `C = std / mean` of intensity over a spatial ROI in a single frame,
- `β` is the **speckle contrast factor** of the optical+laser system
  (set by speckle-grain size vs pixel, polarization, mode count, etc.;
  NOT by sample dynamics),
- `τ` is the field decorrelation time of the dynamic scatterer,
- `ε` is a noise/static-scatter floor (shot, read, fixed-pattern).

Limits:
- `T ≪ τ` (fast decorrelation regime): `C → β · √(τ/T)`, i.e. `C² ∝ τ/T`.
  This is the source of the clinical "blood-flow index" `BFI = 1/(C²·T)`.
- `T ≫ τ` (slow decorrelation regime): `C → β`. The integrated frame is a
  single frozen speckle pattern.

## 2. β — what reduces it

β is the multiplicative ceiling on contrast for fully resolved, fully
coherent, fully polarized speckle. Anything that adds an independent speckle
realization at the sensor averages it down by ~1/√N. Known contributors:

- **Speckle-grain undersampling**: if a pixel covers `K` speckle grains,
  β drops by ~1/√K. Set by camera aperture, magnification, working distance.
- **Polarization mixing**: depolarized scattering puts orthogonal-polarization
  speckle on the sensor; two independent patterns → factor √2 unless an
  analyzer is placed before the camera.
- **Laser transverse modes (TEM_mn)**: each mode produces its own
  independent speckle pattern. M transverse modes → β scales by ~1/√M.
- **Laser coherence length vs sample path-length spread** (the big one
  for multiply-scattering samples — see §4).

**Practical implication**: any change to the optical path (objective, ND
filters, polarizer, working distance) or laser invalidates the existing β
calibration. Re-measure β on a static scatterer after any such change.

## 3. Measuring β with a static-scatterer sweep

A fully static scatterer (paper, ground glass, Spectralon) has `τ_sample → ∞`,
so the LSCI formula collapses to `C(T) → β`. Run the same exposure sweep used
for the dynamic sample; the plateau value of `C(T)` is β for that optical
setup.

Caveats:
- The shortest exposures (mean signal close to the 64-DN black-level pedestal)
  are inflated by read noise. Discard those points for the plateau fit.
- The longest exposures may saturate the 12-bit ADC (ceiling 65408 in the
  Mono16 container). Attenuate accordingly.
- Paper is fine for a surface scatterer reference. Ground glass is more
  reproducible if you need to track β across days/weeks.

## 4. Laser-induced decorrelation: the second LSCI channel

When the laser is not infinitely coherent on the LSCI timescale, the field
autocorrelation factorizes:

```
g₁_total(τ) = g₁_sample(τ) · g₁_laser(τ)
```

For exponential decays, the apparent decorrelation rate is the sum:

```
1 / τ_apparent  =  1 / τ_sample  +  1 / τ_laser
```

τ_apparent **saturates at τ_laser** when τ_sample → ∞. This is the key
mechanism for the "non-scaling nonlinearity" between two lasers measuring
the same dynamic sample: even after a β rescale, the laser with shorter
τ_laser hits a ceiling that the longer-coherence laser doesn't.

### Sources of τ_laser in the LSCI band

Mode-bandwidth coherence (ns or shorter) **does not** contribute to τ_laser
in the LSCI sense — it averages into β within every exposure, regardless of
T. What matters is laser instability on µs–ms timescales:

- **Mode hopping** (cheap unstabilized diodes): wavelength jumps between
  adjacent cavity modes due to thermal drift. Timescale µs–s — right in the
  LSCI band. Catching a hop mid-exposure adds a real decorrelation channel.
- **Slow frequency drift**: minutes-scale, usually negligible for LSCI run
  durations.
- **Intensity 1/f noise**: can mimic decorrelation if it's large enough.
  Mostly cosmetic for LSCI; doesn't change τ.

### Coherence-length-vs-path-length (a β effect, not a τ effect)

Distinct from τ_laser but easy to confuse with it. In multiply-scattering
media (milk, intralipid, tissue), photons take a distribution of path
lengths. Photons whose path-length spread exceeds ℓ_coh add as **incoherent
background** rather than contributing to the speckle. Result: β drops.

For paper or other surface scatterers, path-length spread is microns → all
light is coherent at the sensor → β stays high.

For milk/tissue with mm–cm of path spread, ℓ_coh matters dramatically:
- HeNe (typical 30-cm cavity, ~1.5 GHz Doppler-broadened bandwidth):
  ℓ_coh ≈ 20 cm → all multiply-scattered light coherent → high β.
- Cheap 650 nm Fabry-Perot diode (1–3 nm bandwidth):
  ℓ_coh ≈ 100–300 µm → only ballistic shell of photons contributes → β can
  collapse to 0.1 or lower in milk.

**As the milk coagulates**, the optical-path-length distribution changes
(scattering coefficient, penetration depth, fraction of backscatter all
shift). The fraction of light "within ℓ_coh" therefore changes, so
**β itself drifts during the run**, and the drift differs between lasers
with different ℓ_coh. This is a second non-scaling nonlinearity that
*cannot* be removed by τ_laser deconvolution.

### Diagnostic: is the nonlinearity τ_laser or ℓ_coh / β(t)?

| observed across two lasers | likely cause | fix |
|---|---|---|
| agree at short T, diverge at long T (after β rescale) | finite τ_laser, different per laser | deconvolve `1/τ_sample = 1/τ_apparent − 1/τ_laser` per laser |
| disagree at short T as coagulation progresses (β rescale factor itself drifts in time) | ℓ_coh vs path-length spread; β(t) modulation | use longer-coherence laser; cannot deconvolve away |

## 5. Expected coherence numbers

### HeNe (typical lab tube, unstabilized, ~30 cm cavity)
- Doppler bandwidth (Ne, room T): Δν ≈ 1.5 GHz FWHM
- Cavity FSR (c/2L): ~500 MHz → typically 3 longitudinal modes oscillating
- τ_coh ≈ 1/Δν ≈ 0.7 ns
- ℓ_coh ≈ 20 cm
- **Class A laser**: no relaxation oscillations, very low intensity noise
- **For LSCI**: τ_coh ≪ T_min (8 µs) → mode beating averages into β only.
  Effectively "white" on the camera-exposure timescale. The gold-standard
  LSCI source.

### Cheap 650 nm Fabry-Perot diode (pointer-style, no TEC, no stabilization)
- Cavity length ~300 µm, n ≈ 3.5 → FSR ≈ 150 GHz → 5–20 modes lasing
- Total bandwidth ~1–3 nm = ~700 GHz–2 THz
- τ_coh ≈ 0.5–1 ps
- ℓ_coh ≈ 100–300 µm
- **Class B laser**: GHz-scale relaxation oscillations (average out within
  exposure), plus 1/f intensity noise
- **Mode hopping**: µs–s timescale, in the LSCI band → real τ_laser
  contribution that *will* show up as `C(T)` rolloff on paper
- **ℓ_coh ≪ multiply-scattered path-length spread** in milk → β catastrophe
  on top of the τ_laser issue
- Mitigations: TEC + current stabilization to suppress mode hops; accept
  reduced β; or use a single-mode stabilized variant (DBR, DFB, VBG-locked).

## 6. Calibration recipe (per laser, per optical setup)

Both numbers (`β`, `τ_laser`) come from offline multi-exposure measurements
on a static scatterer; the actual experiment can then run at a single fixed
exposure.

1. **β** — paper sweep, take the short-T plateau (after discarding read-noise-
   inflated points). Set `T_min` low enough that you're well into the
   "frozen-speckle" regime where the plateau is clean.
2. **τ_laser** — same paper, extended exposures. If `C(T)` stays flat all the
   way out to the longest practical T, `τ_laser` is effectively infinite for
   the experiment. If it droops, fit the LSCI form with sample term zero
   (β fixed from step 1) to extract τ_laser.
3. **Cross-check**: fully-coagulated milk should give the same `τ_laser` as
   paper (`τ_sample` ≈ ∞ in a set gel). If the two disagree, you have an
   ℓ_coh-vs-path-length β(t) effect on the milk that paper doesn't show.

Whenever the optical path changes, re-do steps 1–2. β is *very* sensitive to
geometry; τ_laser depends only on the laser itself.

## 7. Inverting C → τ_apparent at a single exposure

Single-exposure operation is fine; you can invert the LSCI formula numerically
per frame, provided β is known. Without β the inversion has no anchor.

The function to invert:

```
f(x) = sqrt[ x · (1 − exp(−2/x)) ]      where x = τ/T
```

Solve `f(x) = C/β` for `x`, then `τ_apparent = x · T`. Then deconvolve:

```
1/τ_sample = 1/τ_apparent − 1/τ_laser
```

### Properties of f(x)

- Monotonically increasing on `x ∈ (0, ∞)`. Unique root for any `0 < C/β < 1`.
- Range: `f(x) ∈ (0, 1)`. `C/β ≥ 1` means calibration is off (β too small
  or noise pushed C past β); flag or clip.
- `C/β → 1` is ill-conditioned: `dC/dτ → 0`, so small noise in C → huge
  errors in τ. Flag as "τ ≫ T, only a lower bound."

### Asymptotes (useful for sanity / limits)

| regime | τ ≈ |
|---|---|
| `C/β ≪ 1` (fast decorrelation, liquid milk) | `T · (C/β)²` |
| `C/β ≈ 1` (slow decorrelation, set gel) | `T / (3·(1 − C/β))` |

### Numerical methods

- **`scipy.optimize.brentq`** on `g(x) = f(x) − C/β`. Robust, derivative-free,
  guaranteed convergence with a valid bracket. ~10 µs per inversion — fine for
  per-frame ROI-level data.
- **Lookup table + `np.interp`**: precompute `(x_grid, f(x_grid))` once on a
  log-spaced `x_grid`, then `np.interp(C/β, y_grid, x_grid)` inverts a whole
  image in sub-ms. Use this for full-image LSCI maps.

```python
import numpy as np
from scipy.optimize import brentq

def contrast_model(x):
    return np.sqrt(x * (1.0 - np.exp(-2.0 / x)))

def invert_contrast_scalar(C, beta, T, bracket=(1e-4, 1e4)):
    target = C / beta
    if target <= 0:   return 0.0
    if target >= 1:   return np.inf
    x = brentq(lambda x: contrast_model(x) - target, *bracket)
    return x * T

# Vectorised via LUT (for whole-image data):
x_grid = np.logspace(-4, 4, 4096)
y_grid = contrast_model(x_grid)

def invert_contrast_vec(C, beta, T):
    target = np.clip(C / beta, y_grid[0], y_grid[-1])
    x = np.interp(target, y_grid, x_grid)
    return x * T
```

## 8. From τ_sample to viscosity

### Single-scattering (textbook DLS) limit

```
g₁(τ) = exp(−D·q²·τ)         →   τ_sample = 1 / (D · q²)
D = k_B·T_K / (6π·η·r)        (Stokes–Einstein)
q = (4π·n / λ) · sin(θ/2)
```

So `τ_sample = 6π·η·r / (k_B·T_K·q²)` and at fixed T_K, λ, n, θ, r:

```
τ_sample  ∝  η
```

Order-of-magnitude check: HeNe 632.8 nm, water (n ≈ 1.33), backscatter,
100 nm radius, 25 °C → q ≈ 2.6×10⁷ m⁻¹, D ≈ 2.2×10⁻¹² m²/s,
τ_sample ≈ 0.6 ms. Matches typical "liquid milk" LSCI numbers.

### Multiple-scattering (DWS) regime

For a multiply-scattering medium in backscattering:

```
g₁(τ) ≈ exp[−γ · sqrt(6τ / τ₀)]      τ₀ = 1/(D·k₀²),  k₀ = 2π·n/λ
τ_sample,DWS  ~  τ₀ · (ℓ* / ⟨s⟩)²
```

Same `τ_sample ∝ η · r / T_K`, with the prefactor including the
transport-mean-free-path-to-photon-path ratio. As long as ℓ* and the
geometry stay stationary, the proportionality is constant.

### The catch for coagulating milk

Both η and r_eff change during coagulation:
- Casein micelles aggregate → r_eff grows (~r_cluster ≈ N^(1/d_f)·r_micelle
  for a fractal of dimension d_f).
- Network forms → bulk η grows.
- Post-gel-point: motion becomes subdiffusive, Stokes–Einstein in its
  classical form breaks down; what's measured is a viscoelastic combination
  of plateau modulus and characteristic relaxation time.

So **τ_sample tracks the product `η · r_eff`, not η alone**, and the
proportionality constant drifts during the run.

### What's actually extractable from LSCI of coagulating milk

| desired quantity | LSCI gives directly? |
|---|---|
| Absolute Newtonian η(t) in mPa·s | **No** — confounded with r_eff(t); needs external calibration (rheometer cross-calibration on the same milk) or independent particle sizing |
| Gel point | **Yes** — inflection / slope change in τ_sample(t) is robust |
| Set time | **Yes** — threshold on τ_sample(t) or its derivative |
| Final firmness | **Yes** — τ_sample plateau at end of run |
| Relative changes during one run | **Yes** — monotonic in η (and in r_eff) |
| Cross-batch comparisons | **Yes if** β and τ_laser are calibrated per-batch per-laser |

For most colleagues asking "what's the viscosity from LSCI," the practical
deliverable is one of the transition points (gel time, set time, firmness)
rather than an absolute η in mPa·s. Those are derivable without resolving
the η-vs-r_eff ambiguity.

## 9. Practical workflow summary

1. Set up optical path (laser, ND filters, polarizer, objective, working
   distance). Lock down everything.
2. **β** ← paper sweep, short-T plateau.
3. **τ_laser** ← paper sweep, long-T tail. If flat to seconds, treat as ∞.
4. (Optional) cross-check on fully-coagulated milk. Disagreement with paper
   indicates ℓ_coh/path-length-spread β(t) drift in the dynamic sample.
5. Run the coagulation experiment at a single chosen `T_fixed` (videos).
   Choose `T_fixed` ≪ τ_laser if possible to keep the deconvolution gentle.
6. Per frame: `C → τ_apparent` (Brent or LUT) → `τ_sample` (subtract
   1/τ_laser).
7. Interpret τ_sample(t): identify gel point, set time, firmness, or fit
   to a coagulation kinetics model.
8. Re-do steps 2–3 whenever the optical path or laser changes.
