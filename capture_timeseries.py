#!/usr/bin/env python3
"""
Long, drop-free speckle time series: contrast in two ROIs, plus optional raw frames.

The GUI viewer cannot do this. Its display loop calls SpeckleCamera.latest_frame(),
which is *defined* to discard: it drains the queue and recycles every buffer but
the newest. Saving every frame is not an option you add to that loop, it is a
different loop -- this one, which pops every buffer and never skips.

What it writes into --out:

    contrast.csv    one row per frame: time, frame_id, contrast per ROI, ratio
    frames.raw      raw sensor frames, appended back to back (optional)
    frames.json     shape/dtype/region/decimation, so frames.raw can be read back
    run.json        the full settings and the drop audit for the run

Raw frames are the expensive part, so they are optional and decimatable:

    --raw-every 1     every frame          (~2.6 GB/min at the USB 2.0 ceiling)
    --raw-every 20    one frame in 20      (~130 MB/min)
    --raw-every 0     none at all          (contrast only; runs for hours)

Decimation never changes the contrast series: every frame is still popped and
measured, only the *writing* of raw pixels is skipped. That is also why it costs
nothing to leave contrast in the hot loop.

Example, taking the ROI positions off the viewer's readout ("at: 300px (row 874,
col 618)" means --roi1 618,874):

    python3 capture_timeseries.py --minutes 5 --roi-size 300 \\
        --roi1 618,874 --roi2 2154,874 --crop --raw-every 20 --out run_2026-09-10

Read frames.raw back with:

    meta = json.load(open("frames.json"))
    frames = np.memmap("frames.raw", dtype=meta["dtype"],
                       mode="r").reshape(-1, meta["height"], meta["width"])
"""

# --- macOS / Homebrew env bootstrap (same trick as speckle_viewer.py) ---------
import os
import sys

if sys.platform == "darwin" and os.environ.get("_ARAVIS_BOOTSTRAP") != "1":
    brew = "/opt/homebrew"
    pyver = f"python{sys.version_info.major}.{sys.version_info.minor}"
    extra = {
        "DYLD_FALLBACK_LIBRARY_PATH": f"{brew}/lib",
        "PYTHONPATH": f"{brew}/lib/{pyver}/site-packages",
        "GI_TYPELIB_PATH": f"{brew}/lib/girepository-1.0",
    }
    env = os.environ.copy()
    for key, value in extra.items():
        env[key] = f"{value}:{env[key]}" if env.get(key) else value
    env["_ARAVIS_BOOTSTRAP"] = "1"
    os.execve(sys.executable, [sys.executable, *sys.argv], env)
# -----------------------------------------------------------------------------

import argparse
import csv
import json
import signal
import time
from pathlib import Path

import numpy as np

import gi
gi.require_version("Aravis", "0.8")
from gi.repository import Aravis

from speckle_viewer import N_ROIS, SpeckleCamera, TILES_PER_AXIS, roi_stats

# A far deeper ring than the viewer's 48: this loop must survive a filesystem
# stall (a flush, a directory sync) without the camera starving. At 200 fps, 256
# buffers is ~1.3 s of slack.
N_BUFFERS = 256
POP_TIMEOUT_US = 2_000_000     # a stall longer than this means acquisition died


def parse_point(text):
    """'618,874' -> (618, 874)."""
    try:
        x, y = (int(v) for v in text.split(","))
    except Exception:
        raise argparse.ArgumentTypeError(f"expected x,y (e.g. 618,874), got {text!r}")
    return x, y


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Capture a drop-free speckle contrast time series.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--out", type=Path, required=True, help="output directory")
    dur = p.add_mutually_exclusive_group(required=True)
    dur.add_argument("--seconds", type=float, help="run for this many seconds")
    dur.add_argument("--minutes", type=float, help="run for this many minutes")

    p.add_argument("--raw-every", type=int, default=0, metavar="N",
                   help="write one raw frame in N; 0 writes none, 1 writes all")
    p.add_argument("--roi-size", type=int, default=300, help="ROI side in px")
    p.add_argument("--roi1", type=parse_point, metavar="X,Y",
                   help="ROI 1 centre in sensor px (default: sensor 1/4 width)")
    p.add_argument("--roi2", type=parse_point, metavar="X,Y",
                   help="ROI 2 centre in sensor px (default: sensor 3/4 width)")
    p.add_argument("--crop", action="store_true",
                   help="read out only the box enclosing both ROIs (faster)")

    p.add_argument("--exposure", type=int, metavar="US", help="exposure, µs")
    p.add_argument("--gain", type=float, metavar="DB", help="gain, dB")
    p.add_argument("--fps", type=float,
                   help="frame rate; omit to ride the ceiling for the region")
    p.add_argument("--pixel-format", default="Mono16", choices=["Mono8", "Mono16"],
                   help="Mono8 halves the bytes per frame, doubling the "
                        "achievable rate on the same link")
    p.add_argument("--tiles", type=int, default=TILES_PER_AXIS,
                   help="contrast is the mean of NxN per-tile std/mean")
    return p.parse_args(argv)


def roi_boxes_in_frame(centers, size, region):
    """Clip each ROI centre (sensor px) to a (top, left, size) box in frame px."""
    x0, y0, w, h = region
    size = int(np.clip(size, 8, min(h, w)))
    boxes = []
    for cx, cy in centers:
        fx, fy = cx - x0, cy - y0            # sensor -> frame coordinates
        left = int(np.clip(fx - size // 2, 0, w - size))
        top = int(np.clip(fy - size // 2, 0, h - size))
        boxes.append((top, left, size))
    return boxes


def bounding_box(centers, size, sensor):
    """Sensor-pixel box enclosing every ROI, clipped to the sensor."""
    sw, sh = sensor
    lefts = [int(np.clip(cx - size // 2, 0, sw - size)) for cx, _ in centers]
    tops = [int(np.clip(cy - size // 2, 0, sh - size)) for _, cy in centers]
    left, top = min(lefts), min(tops)
    right, bottom = max(l + size for l in lefts), max(t + size for t in tops)
    return left, top, right - left, bottom - top


def main(argv=None):
    args = parse_args(argv)
    if args.raw_every < 0:
        raise SystemExit("--raw-every must be 0 or more")
    duration = args.seconds if args.seconds is not None else args.minutes * 60.0
    args.out.mkdir(parents=True, exist_ok=True)

    cam = SpeckleCamera(pixel_format=args.pixel_format, n_buffers=N_BUFFERS)
    sw, sh = cam.sensor_size()
    centers = [args.roi1 or (sw // 4, sh // 2),
               args.roi2 or (3 * sw // 4, sh // 2)]
    if len(centers) != N_ROIS:
        raise SystemExit(f"this script measures {N_ROIS} ROIs")

    if args.crop:
        left, top, bw, bh = bounding_box(centers, args.roi_size, (sw, sh))
        region = cam.set_region_box(left, top, bw, bh)
    else:
        region = cam.set_full_region()
    region = tuple(int(v) for v in region)
    x0, y0, w, h = region

    if args.exposure is not None:
        cam.set_exposure_us(args.exposure)
    if args.gain is not None:
        cam.set_gain_db(args.gain)
    # Without an explicit rate, ride the ceiling this region allows.
    target = args.fps if args.fps is not None else cam.frame_rate_bounds()[1]
    fps = cam.set_frame_rate(float(target))

    boxes = roi_boxes_in_frame(centers, args.roi_size, region)
    dtype = np.uint8 if args.pixel_format == "Mono8" else np.uint16
    bytes_per_frame = w * h * np.dtype(dtype).itemsize
    raw_rate = bytes_per_frame * fps / args.raw_every if args.raw_every else 0.0

    print(cam.description())
    print(f"region      {w}x{h} at ({x0}, {y0})   {args.pixel_format}")
    for i, (top, left, size) in enumerate(boxes):
        print(f"ROI {i + 1}       {size}px at frame (row {top}, col {left})"
              f"  = sensor ({y0 + top}, {x0 + left})")
    print(f"frame rate  {fps:.1f} fps   exposure {cam.get_exposure_us()} us"
          f"   gain {cam.get_gain_db():.2f} dB")
    print(f"duration    {duration:.0f} s  ->  ~{int(fps * duration)} frames")
    if args.raw_every:
        print(f"raw frames  1 in {args.raw_every}  ->  {raw_rate / 1e6:.1f} MB/s, "
              f"~{raw_rate * duration / 1e9:.2f} GB total")
    else:
        print("raw frames  none (contrast only)")
    print()

    stopping = {"now": False}

    def request_stop(sig, frame):
        # Finish the frame in hand, then close the files cleanly: a half-written
        # raw frame would desynchronise every frame after it.
        stopping["now"] = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, request_stop)

    raw_path = args.out / "frames.raw"
    csv_path = args.out / "contrast.csv"
    raw_file = open(raw_path, "wb") if args.raw_every else None

    n_seen = n_written = 0
    n_bad = 0                      # buffers the camera returned with a bad status
    first_id = last_id = None
    id_gaps = []                   # (previous_id, next_id) around each break
    t_start = None

    cam.start()
    c0, f0, u0 = cam.stream_stats()
    try:
        with open(csv_path, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["t_s", "frame_id", "camera_timestamp_ns"]
                            + [f"contrast_roi{i + 1}" for i in range(N_ROIS)]
                            + [f"mean_roi{i + 1}" for i in range(N_ROIS)]
                            + ["ratio_roi1_roi2", "raw_index"])
            deadline = None
            # Wall-clock backstop: the real deadline only starts on the first
            # good frame, so a run that never gets one would otherwise sit here
            # popping bad buffers forever.
            hard_deadline = time.monotonic() + duration + 30.0
            while True:
                buf = cam.stream.timeout_pop_buffer(POP_TIMEOUT_US)
                if buf is None:
                    print("\nacquisition stalled: no buffer within "
                          f"{POP_TIMEOUT_US / 1e6:.0f} s", file=sys.stderr)
                    break
                now = time.monotonic()
                try:
                    # Not `continue` on a bad buffer: that would skip the
                    # deadline check below and a steady stream of bad buffers
                    # would loop forever.
                    if buf.get_status() != Aravis.BufferStatus.SUCCESS:
                        n_bad += 1
                    else:
                        if t_start is None:
                            t_start, deadline = now, now + duration
                        frame = (np.frombuffer(buf.get_data(), dtype=dtype)
                                 .reshape(buf.get_image_height(),
                                          buf.get_image_width()))

                        fid = int(buf.get_frame_id())
                        if last_id is not None and fid != last_id + 1:
                            id_gaps.append((last_id, fid))
                        if first_id is None:
                            first_id = fid
                        last_id = fid

                        stats = [roi_stats(frame, top, left, size, cam.sat_level,
                                           n_tiles=args.tiles)
                                 for (top, left, size) in boxes]
                        c = [s["contrast"] for s in stats]
                        ratio = c[0] / c[1] if c[1] > 0 else float("nan")

                        raw_index = ""
                        if raw_file is not None and n_seen % args.raw_every == 0:
                            # tobytes() copies: the buffer memory is reused as
                            # soon as it is pushed back.
                            raw_file.write(frame.tobytes())
                            raw_index = n_written
                            n_written += 1

                        writer.writerow([f"{now - t_start:.6f}", fid,
                                         int(buf.get_timestamp())]
                                        + [f"{v:.6f}" for v in c]
                                        + [f"{s['mean']:.2f}" for s in stats]
                                        + [f"{ratio:.6f}", raw_index])
                        n_seen += 1
                finally:
                    cam.stream.push_buffer(buf)

                if t_start is not None and n_seen % 200 == 0:
                    elapsed = now - t_start
                    print(f"\r{elapsed:6.1f}s  {n_seen} frames  "
                          f"{n_seen / max(elapsed, 1e-9):6.1f} fps  "
                          f"{n_written} raw  {len(id_gaps)} gaps", end="", flush=True)
                if (stopping["now"] or now >= hard_deadline
                        or (deadline is not None and now >= deadline)):
                    break
    finally:
        c1, f1, u1 = cam.stream_stats()
        cam.stop()
        if raw_file is not None:
            raw_file.close()

    elapsed = (time.monotonic() - t_start) if t_start else 0.0
    lost_to_gaps = sum(b - a - 1 for a, b in id_gaps)
    failures, underruns = f1 - f0, u1 - u0
    clean = not id_gaps and failures == 0 and underruns == 0 and n_bad == 0

    meta = dict(width=w, height=h, dtype=np.dtype(dtype).name, region=list(region),
                raw_every=args.raw_every, n_raw_frames=n_written,
                pixel_format=args.pixel_format)
    (args.out / "frames.json").write_text(json.dumps(meta, indent=2))
    (args.out / "run.json").write_text(json.dumps(dict(
        meta, roi_centers=[list(c) for c in centers], roi_size=args.roi_size,
        roi_boxes=[list(b) for b in boxes], tiles=args.tiles,
        exposure_us=cam.get_exposure_us(), gain_db=cam.get_gain_db(),
        frame_rate=fps, duration_s=elapsed, n_frames=n_seen,
        frame_id_first=first_id, frame_id_last=last_id,
        frame_id_gaps=[list(g) for g in id_gaps], frames_lost_to_gaps=lost_to_gaps,
        stream_failures=failures, stream_underruns=underruns,
        bad_status_buffers=n_bad, no_frames_dropped=clean), indent=2))

    print(f"\n\n{n_seen} frames in {elapsed:.1f}s "
          f"({n_seen / max(elapsed, 1e-9):.1f} fps)")
    print(f"contrast    {csv_path}")
    if raw_file is not None:
        print(f"raw frames  {n_written} written to {raw_path} "
              f"({raw_path.stat().st_size / 1e9:.2f} GB)")
    # Two independent drop checks: the library's counters know what it lost, a
    # frame_id gap also catches frames the camera never delivered at all.
    print(f"frame_id    {first_id} -> {last_id}, {len(id_gaps)} gap(s), "
          f"{lost_to_gaps} frame(s) missing")
    print(f"stream      {failures} failures, {underruns} underruns, "
          f"{n_bad} bad-status buffers")
    print("NO FRAMES DROPPED" if clean else "!! FRAMES WERE DROPPED -- see run.json")
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
