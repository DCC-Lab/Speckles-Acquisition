#!/usr/bin/env python3
"""
Stitch two multi-exposure contrast runs (a BRIGHT + a DIM sweep of the SAME
optical alignment, differing only in attenuation) into one contrast-vs-exposure
curve.

Why stitch: a single attenuation can't span the whole exposure range cleanly.
Long exposures saturate the bright run; short exposures leave the dim run down
near the black-level pedestal, where shot/read noise inflates std/mean. The fix
is to take, at each exposure, the run whose ROI signal is high enough to be
noise-free but not yet saturated.

Both runs MUST be analysed with the SAME pinned ROI (analyze_sweep.py FIXED_ROI)
-- contrast is region-dependent, so a different ROI per run is not comparable.

Selection per exposure:
  - drop saturated points (saturated flag or roi_max >= SATURATION),
  - drop pedestal/low-signal points (ROI mean < MIN_MEAN), where contrast is
    inflated by camera noise,
  - if both runs survive (the overlap), keep the higher-mean (brighter, lower
    relative noise) one. In a noise-free world the two would agree there; any
    residual gap is reported as a diagnostic.

Usage:
    python3 stitch_runs.py BRIGHT.csv DIM.csv [-o OUT.csv]
"""

import argparse
import csv
from pathlib import Path

PEDESTAL = 64        # 16-bit black-level DN (BlackLevel = 0%)
MIN_MEAN = 200       # ROI mean must exceed this to trust contrast
SATURATION = 65000   # match analyze_sweep.py


def load(path):
    rows = {}
    with open(path) as f:
        for r in csv.DictReader(f):
            exp = int(r["exposure_us"])
            rows[exp] = {
                "mean": float(r["mean"]),
                "contrast": float(r["contrast_mean"]),
                "contrast_std": float(r["contrast_std"]),
                "roi_max": int(r["roi_max"]),
                "saturated": int(r["saturated"]),
                "roi": (int(r["roi_top"]), int(r["roi_left"]), int(r["roi_size"])),
            }
    return rows


def usable(pt):
    return (not pt["saturated"]
            and pt["roi_max"] < SATURATION
            and pt["mean"] >= MIN_MEAN)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bright", help="contrast.csv from the brighter (less attenuated) run")
    ap.add_argument("dim", help="contrast.csv from the dimmer (more attenuated) run")
    ap.add_argument("-o", "--out", default="contrast_stitched.csv")
    args = ap.parse_args()

    bright = load(args.bright)
    dim = load(args.dim)

    # ROI must match or the contrasts aren't comparable.
    brois = {v["roi"] for v in bright.values()}
    drois = {v["roi"] for v in dim.values()}
    if brois != drois or len(brois) != 1:
        raise SystemExit(f"ROI mismatch: bright {brois} vs dim {drois}.\n"
                         "Re-analyze both runs with the same FIXED_ROI in "
                         "analyze_sweep.py before stitching.")
    roi = next(iter(brois))

    exposures = sorted(set(bright) | set(dim))

    print(f"ROI (top,left,size) : {roi}  (must match across runs)")
    print(f"bright points       : {len(bright)}   dim points: {len(dim)}")
    print(f"MIN_MEAN            : {MIN_MEAN} DN  (pedestal {PEDESTAL})")
    print()
    print(f"{'exp (us)':>9}  {'bright C':>9}  {'dim C':>9}  "
          f"{'-> C':>8}  {'src':>6}  note")
    print("-" * 74)

    overlap = []
    out_rows = []
    for exp in exposures:
        b = bright.get(exp)
        d = dim.get(exp)
        bu = b is not None and usable(b)
        du = d is not None and usable(d)
        bc = f"{b['contrast']:.4f}" if b else "   --   "
        dc = f"{d['contrast']:.4f}" if d else "   --   "

        chosen = src = None
        note = ""
        if bu and du:
            overlap.append((exp, d["contrast"] - b["contrast"]))
            chosen, src = (b, "bright") if b["mean"] >= d["mean"] else (d, "dim")
            note = f"overlap (dim-bright={d['contrast'] - b['contrast']:+.4f})"
        elif bu:
            chosen, src = b, "bright"
        elif du:
            chosen, src = d, "dim"
        else:
            # explain why it was dropped
            reasons = []
            for tag, pt in (("bright", b), ("dim", d)):
                if pt is None:
                    continue
                if pt["saturated"] or pt["roi_max"] >= SATURATION:
                    reasons.append(f"{tag} sat")
                elif pt["mean"] < MIN_MEAN:
                    reasons.append(f"{tag} low-signal({pt['mean']:.0f})")
            note = "dropped: " + ", ".join(reasons)

        if chosen is not None:
            print(f"{exp:>9d}  {bc:>9}  {dc:>9}  {chosen['contrast']:>8.4f}  "
                  f"{src:>6}  {note}")
            out_rows.append((exp, chosen["contrast"], chosen["contrast_std"],
                             chosen["mean"], src))
        else:
            print(f"{exp:>9d}  {bc:>9}  {dc:>9}  {'--':>8}  {'--':>6}  {note}")

    out = Path(args.out)
    with out.open("w") as f:
        f.write("exposure_us,contrast,contrast_std,mean,source\n")
        for exp, c, cstd, mean, src in out_rows:
            f.write(f"{exp},{c:.6f},{cstd:.6f},{mean:.6f},{src}\n")

    print()
    if overlap:
        diffs = [d for _, d in overlap]
        n_pos = sum(1 for d in diffs if d > 0)
        print(f"Overlap: {len(overlap)} exposures, dim-minus-bright ΔC "
              f"mean {sum(diffs)/len(diffs):+.4f}, "
              f"range [{min(diffs):+.4f}, {max(diffs):+.4f}]")
        if n_pos == len(overlap):
            print("  ΔC > 0 at every overlap point: the dim run reads high because")
            print("  its lower signal inflates std/mean (shot/read noise). The")
            print("  brighter run is taken as truth in the overlap.")
    else:
        print("No overlap exposures where both runs were trustworthy.")
    print(f"\nWrote {out} ({len(out_rows)} rows)")


if __name__ == "__main__":
    main()
