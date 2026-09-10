#!/usr/bin/env python3
"""
Long, drop-free speckle time series: contrast in two ROIs, plus optional raw frames.

The GUI viewer cannot do this. Its display loop calls SpeckleCamera.latest_frame(),
which is *defined* to discard: it drains the queue and recycles every buffer but
the newest. Saving every frame is not an option you add to that loop, it is a
different loop -- this one, which pops every buffer and never skips.

What it writes into --out:

    contrast.csv    one row per frame: time, frame_id, contrast per ROI, ratio
    contrast.png    both contrasts and their ratio against time

Both are kept current *while the run is in progress*: every row is flushed as it
is measured, and the plot is redrawn every --plot-every seconds (5 s by default)
to the same file. Point an image viewer at contrast.png and watch a multi-hour
acquisition live; tail contrast.csv, or open it in another process, at any time.
A run killed halfway still leaves everything written up to that point.
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

The ROIs come from the viewer by default: speckle_viewer.py keeps roi_state.json
matching what is on screen, so place the two ROIs there, leave it running or not,
and this script measures the same two spots. --roi1/--roi2/--roi-size override it.

    python3 capture_timeseries.py --minutes 5 --crop --raw-every 20 \\
        --out run_2026-09-10

Read frames.raw back with:

    meta = json.load(open("frames.json"))
    frames = np.memmap("frames.raw", dtype=meta["dtype"],
                       mode="r").reshape(-1, meta["height"], meta["width"])
"""

# --- macOS / Homebrew env bootstrap (same trick as speckle_viewer.py) ---------
import os
import sys

if sys.platform == "darwin" and os.environ.get("_ARAVIS_BOOTSTRAP") != "1":
    brew = "/opt/homebrew" if os.uname().machine == "arm64" else "/usr/local"
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
import threading
import time
from pathlib import Path

import numpy as np

import gi
gi.require_version("Aravis", "0.8")
from gi.repository import Aravis

from speckle_viewer import (N_ROIS, RATIO_COLOR, ROI_COLORS, ROI_STATE_PATH,
                            SpeckleCamera, TILES_PER_AXIS, _hex, roi_stats)

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
    p.add_argument("--roi-size", type=int, help="ROI side in px")
    p.add_argument("--roi1", type=parse_point, metavar="X,Y",
                   help="ROI 1 centre in sensor px")
    p.add_argument("--roi2", type=parse_point, metavar="X,Y",
                   help="ROI 2 centre in sensor px")
    p.add_argument("--roi-state", type=Path, default=ROI_STATE_PATH,
                   help="ROI geometry saved by the viewer; --roi1/--roi2/"
                        "--roi-size override whatever it holds")
    p.add_argument("--no-roi-state", action="store_true",
                   help="ignore the viewer's file and use the defaults")
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
    p.add_argument("--plot-every", type=float, default=5.0, metavar="SECONDS",
                   help="redraw contrast.png this often while running; "
                        "0 draws it only at the end")
    p.add_argument("--no-plot", action="store_true",
                   help="never write contrast.png")
    return p.parse_args(argv)


def write_plot(path, t, contrasts, ratio, subtitle):
    """Contrasts in the top panel, their ratio in the bottom one, shared x axis.

    Deliberately not the GUI's twin-axis layout. On a twin axis the ratio's own
    scale makes it sweep right through the band between the two contrast curves,
    which reads as the ratio crossing them; it never does. The GUI accepts that
    because its plot is tiny. Here there is room to separate them, so the panels
    are stacked and each keeps an honest scale.

    Colours come from speckle_viewer, so a curve here means the same ROI as the
    rectangle of that colour in the GUI.

    matplotlib is imported here, not at module scope: a capture must never fail
    at the finish line because plotting is unavailable.
    """
    import matplotlib
    matplotlib.use("Agg")                    # no display: this runs headless
    import matplotlib.pyplot as plt

    t = np.asarray(t, float)
    # Frame-to-frame contrast noise buries the trend on a long run. Keep every
    # sample visible but lay a rolling mean, ~1% of the run, over it.
    window = max(5, len(t) // 100) if len(t) > 1000 else 1
    smooth_t = t[window - 1:] if window > 1 else t

    def smooth(y):
        # np.convolve propagates NaN across the whole window, so a single NaN
        # ratio sample would erase a stretch of the mean. Interpolate first.
        y = np.asarray(y, float)
        bad = ~np.isfinite(y)
        if bad.all():
            return np.full(len(smooth_t), np.nan)
        if bad.any():
            y = y.copy()
            y[bad] = np.interp(np.flatnonzero(bad), np.flatnonzero(~bad), y[~bad])
        return np.convolve(y, np.ones(window) / window, mode="valid")

    # constrained layout, not tight_layout: the latter warns and mislays the
    # panels when the gridspec carries an explicit hspace.
    fig, (ax, rax) = plt.subplots(
        2, 1, figsize=(11, 6.5), sharex=True, layout="constrained",
        gridspec_kw=dict(height_ratios=[2, 1]))

    for i, y in enumerate(contrasts):
        color = _hex(ROI_COLORS[i])
        ax.plot(t, y, "-", color=color, lw=0.7,
                alpha=0.3 if window > 1 else 1.0,
                label=None if window > 1 else f"ROI {i + 1}")
        if window > 1:
            ax.plot(smooth_t, smooth(y), "-", color=color, lw=1.6,
                    label=f"ROI {i + 1}")

    rax.plot(t, ratio, "-", color=RATIO_COLOR, lw=0.7,
             alpha=0.3 if window > 1 else 1.0)
    if window > 1:
        rax.plot(smooth_t, smooth(ratio), "-", color=RATIO_COLOR, lw=1.4)

    ax.set_ylabel("speckle contrast  σ/⟨I⟩")
    ax.legend(loc="best", fontsize="small", framealpha=0.8)
    ax.grid(alpha=0.25)
    rax.set_ylabel("ratio ROI 1 / ROI 2")
    rax.set_xlabel("time (s)")
    rax.grid(alpha=0.25)
    rax.set_xlim(t[0], t[-1])

    smoothed = (f"thin: every frame,  thick: {window}-frame rolling mean"
                if window > 1 else "every frame")
    ax.set_title(f"Speckle contrast time series  ({smoothed})\n{subtitle}",
                 fontsize=10)
    fig.savefig(path, dpi=130, format="png")   # explicit: temp files end .tmp
    plt.close(fig)


class LivePlotter:
    """Redraws contrast.png every `period` seconds, off the acquisition thread.

    Plotting a long series costs hundreds of ms, which is far too long to spend
    in the loop that has to keep popping buffers. It runs on its own thread and
    reads a prefix snapshot of the sample lists.

    No lock: the acquisition thread only ever appends, list.append and slicing
    are atomic under the GIL, and taking the shortest common length gives a
    consistent prefix even if a frame is half-recorded across the lists.
    """

    def __init__(self, path, period, series_t, series_c, series_ratio, subtitle):
        self.path, self.period = path, period
        self.t, self.c, self.ratio = series_t, series_c, series_ratio
        self.subtitle = subtitle
        self.stop = threading.Event()
        self.errors = 0
        self.n_drawn = 0
        self.thread = threading.Thread(target=self._run, daemon=True)

    def snapshot(self):
        n = min([len(self.t), len(self.ratio)] + [len(y) for y in self.c])
        return self.t[:n], [y[:n] for y in self.c], self.ratio[:n]

    def draw_now(self):
        t, c, ratio = self.snapshot()
        if len(t) < 2:
            return False
        # Write and rename: an image viewer watching the file must never catch
        # a half-written PNG.
        tmp = self.path.with_name(self.path.name + ".tmp")
        write_plot(tmp, t, c, ratio, self.subtitle(len(t), t[-1]))
        os.replace(tmp, self.path)
        self.n_drawn += 1
        return True

    def _run(self):
        while not self.stop.wait(self.period):
            try:
                self.draw_now()
            except Exception:
                # A live redraw is a convenience; never let it end the capture.
                self.errors += 1

    def start(self):
        self.thread.start()

    def finish(self):
        self.stop.set()
        self.thread.join(timeout=30)


def load_roi_state(path):
    """The viewer's saved geometry, or None if there is nothing usable there."""
    try:
        state = json.loads(Path(path).read_text())
        centers = [tuple(int(v) for v in c) for c in state["roi_centers_sensor"]]
        size = int(state["roi_size"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if len(centers) != N_ROIS:
        return None
    return centers, size, state


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

    # Precedence: explicit arguments, then whatever the viewer last had on
    # screen, then a default spread across the sensor.
    saved = None if args.no_roi_state else load_roi_state(args.roi_state)
    if saved is not None:
        saved_centers, saved_size, saved_state = saved
        origin = f"viewer state {args.roi_state}"
        age = time.time() - saved_state.get("saved_at", 0)
        origin += f" (saved {age / 60:.0f} min ago)" if age > 90 else " (just saved)"
    else:
        saved_centers = [(sw // 4, sh // 2), (3 * sw // 4, sh // 2)]
        saved_size = 300
        origin = ("--no-roi-state" if args.no_roi_state
                  else f"defaults ({args.roi_state} not readable)")

    centers = [args.roi1 or saved_centers[0], args.roi2 or saved_centers[1]]
    roi_size = args.roi_size if args.roi_size is not None else saved_size
    overridden = [n for n, v in (("--roi1", args.roi1), ("--roi2", args.roi2),
                                 ("--roi-size", args.roi_size)) if v is not None]
    if len(centers) != N_ROIS:
        raise SystemExit(f"this script measures {N_ROIS} ROIs")

    if args.crop:
        left, top, bw, bh = bounding_box(centers, roi_size, (sw, sh))
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

    boxes = roi_boxes_in_frame(centers, roi_size, region)
    dtype = np.uint8 if args.pixel_format == "Mono8" else np.uint16
    bytes_per_frame = w * h * np.dtype(dtype).itemsize
    raw_rate = bytes_per_frame * fps / args.raw_every if args.raw_every else 0.0

    print(cam.description())
    print(f"ROI source  {origin}")
    if overridden:
        print(f"            overridden on the command line: {', '.join(overridden)}")
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

    series_t, series_c = [], [[] for _ in range(N_ROIS)]
    series_ratio = []
    plotter = None
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
            def subtitle(n, secs):
                return (f"{n} frames, {n / max(secs, 1e-9):.1f} fps, "
                        f"{roi_size}px ROIs at {tuple(centers[0])} and "
                        f"{tuple(centers[1])}, {args.pixel_format}, "
                        f"{cam.get_exposure_us()} us")

            if not args.no_plot and args.plot_every > 0:
                plotter = LivePlotter(args.out / "contrast.png", args.plot_every,
                                      series_t, series_c, series_ratio, subtitle)
                plotter.start()

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
                        series_t.append(now - t_start)
                        for i, v in enumerate(c):
                            series_c[i].append(v)
                        series_ratio.append(ratio)
                        n_seen += 1
                        # Flush every row: the CSV must be complete and readable
                        # from another process at any moment, and a run killed
                        # halfway must keep everything measured so far. One
                        # flush per frame is ~60 syscalls/s, which is nothing.
                        fh.flush()
                        if raw_index != "":
                            raw_file.flush()
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
        if plotter is not None:
            plotter.finish()
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
        meta, roi_centers=[list(c) for c in centers], roi_size=roi_size,
        roi_source=origin,
        roi_boxes=[list(b) for b in boxes], tiles=args.tiles,
        exposure_us=cam.get_exposure_us(), gain_db=cam.get_gain_db(),
        frame_rate=fps, duration_s=elapsed, n_frames=n_seen,
        frame_id_first=first_id, frame_id_last=last_id,
        frame_id_gaps=[list(g) for g in id_gaps], frames_lost_to_gaps=lost_to_gaps,
        stream_failures=failures, stream_underruns=underruns,
        bad_status_buffers=n_bad, no_frames_dropped=clean), indent=2))

    # One last redraw so the file covers every frame, including those measured
    # after the final periodic pass.
    plot_path = args.out / "contrast.png"
    if args.no_plot or len(series_t) < 2:
        plot_path = None
    else:
        try:
            if plotter is None:
                plotter = LivePlotter(plot_path, 0, series_t, series_c,
                                      series_ratio,
                                      lambda n, secs: (
                                          f"{n} frames, {n / max(secs, 1e-9):.1f} fps, "
                                          f"{roi_size}px ROIs at {tuple(centers[0])} "
                                          f"and {tuple(centers[1])}, "
                                          f"{args.pixel_format}, "
                                          f"{cam.get_exposure_us()} us"))
            if not plotter.draw_now():
                plot_path = None
        except Exception as exc:
            # Never let a plotting problem be the thing that ends a long run.
            print(f"could not write {plot_path}: {exc}", file=sys.stderr)
            plot_path = None

    print(f"\n\n{n_seen} frames in {elapsed:.1f}s "
          f"({n_seen / max(elapsed, 1e-9):.1f} fps)")
    print(f"contrast    {csv_path}")
    if plot_path is not None:
        live = (f", redrawn {plotter.n_drawn}x during the run"
                if plotter is not None and plotter.n_drawn else "")
        errs = (f", {plotter.errors} redraw error(s)"
                if plotter is not None and plotter.errors else "")
        print(f"plot        {plot_path}{live}{errs}")
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
