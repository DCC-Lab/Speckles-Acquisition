#!/usr/bin/env python3
"""
Live speckle viewer with real-time contrast in two ROIs.

A minimal image viewer for an Aravis/GenICam camera (e.g. FLIR Blackfly S):
streams frames continuously, lets you click to position two square ROIs, and
reports the speckle contrast (std/mean) of each one in real time, both traced
together on the rolling plot.

UI is built with mytk (https://github.com/DCC-Lab/myTk); camera I/O is Aravis.

Run:
    python3 speckle_viewer.py

Click on the image to move ROI 1, shift-click to move ROI 2; they share the
one "ROI size" setting. Each ROI has its own colour -- green and cyan -- used
for both its rectangle and its curve on the plot.

"Grain acf1" is the lag-1 horizontal autocorrelation of the ROI -- a quick
speckle-grain-size gauge: ~0 means grains ~1 px (undersampled, contrast
suppressed), ~0.5 means grains ~2 px (well sampled). Stop down the imaging
aperture to grow the grains until acf1 ~ 0.5.
"""

# --- macOS / Homebrew env bootstrap (same trick as take_exposure_sweep.py) ---
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

import atexit
import signal
import time
from collections import deque
from tkinter import filedialog, ttk

import numpy as np
from PIL import Image, ImageDraw, ImageTk

import gi
gi.require_version("Aravis", "0.8")
from gi.repository import Aravis

from mytk import (App, Box, Button, Checkbox, FormattedEntry, IntEntry, Label,
                  PopupMenu, XYPlot)
from mytk.base import Base

from matplotlib.ticker import FormatStrFormatter


# =============================================================================
# CONFIG
# =============================================================================
CAMERA_ID = None            # None = first camera found
PIXEL_FORMAT = "Mono16"     # 12-bit sensor data in a 16-bit container (ceiling 65408).
                            # "Mono8" is faster for live view if you don't need depth.
N_STREAM_BUFFERS = 48       # ring of buffers cycled through the stream. The
                            # display drains every REFRESH_MS (~30 ms), so the
                            # ring must hold one interval's worth of frames; at
                            # ~1000 fps (tiny ROI) that's ~30, so 48 leaves
                            # margin. Costs ~N x payload of RAM (Mono16 full
                            # frame ~3 MB each), allocated up front in start().
DISPLAY_MAX_WIDTH = 820     # on-screen image width (px). The display box is a
                            # FIXED size (this width x the sensor aspect ratio);
                            # every frame is scaled to fit inside it, so changing
                            # the ROI never resizes the widget or the window.
DEFAULT_ROI_SIZE = 300
DEFAULT_EXPOSURE_US = 500
REFRESH_MS = 30             # display refresh period
SLOW_MS = 250               # period for plot redraw + applying exposure changes
PLOT_HISTORY_S = 3          # rolling contrast-plot window (seconds)
N_ROIS = 2                  # independent ROIs measured side by side
ROI_COLORS = [(0, 200, 0), (0, 170, 255)]   # overlay RGB, index-matched to N_ROIS.
                            # The plot derives its line colours from these, so a
                            # curve and its rectangle can never drift apart.
RATIO_COLOR = "k"           # ROI1/ROI2 contrast ratio: right-hand axis, dashed
TILES_PER_AXIS = 5          # ROI is split into this many tiles per axis (N×N);
                            # contrast = mean of each tile's std/mean. Tiling
                            # removes ROI-scale illumination gradients.
# =============================================================================


class SpeckleCamera:
    """Thin wrapper over an Aravis camera set up for linear speckle capture."""

    def __init__(self, camera_id=CAMERA_ID, pixel_format=PIXEL_FORMAT,
                 n_buffers=N_STREAM_BUFFERS):
        self.pixel_format = pixel_format
        self.n_buffers = n_buffers
        self.stream = None

        Aravis.update_device_list()
        if Aravis.get_n_devices() == 0:
            raise RuntimeError("No Aravis cameras found (try arv-tool-0.8).")
        if camera_id is None:
            camera_id = Aravis.get_device_id(0)
        self.camera = Aravis.Camera.new(camera_id)
        self._configure()

        # Saturation level in the native dtype (Mono8 -> 255, Mono16 -> ~ceiling).
        self.sat_level = 255 if self._is_mono8() else 65000

        # A camera left acquiring is what breaks the *next* launch: it refuses a
        # PixelFormat write while streaming, with a misleading "access-denied".
        # Cover every interpreter exit, including an unhandled exception.
        atexit.register(self._stop_quietly)

    def _stop_quietly(self):
        """stop() that never raises -- safe at interpreter teardown."""
        try:
            self.stop()
        except Exception:
            pass

    def _is_mono8(self):
        return self.pixel_format == "Mono8"

    def _configure(self):
        cam = self.camera
        cam.set_exposure_time_auto(Aravis.Auto.OFF)
        try:
            cam.set_gain_auto(Aravis.Auto.OFF)
            gain_min, _ = cam.get_gain_bounds()
            cam.set_gain(gain_min)
        except Exception as exc:
            print(f"Warning: could not pin gain to minimum: {exc}")
        if cam.is_feature_available("GammaEnable"):
            cam.set_boolean("GammaEnable", False)
        cam.set_pixel_format_from_string(self.pixel_format)
        cam.set_acquisition_mode(Aravis.AcquisitionMode.CONTINUOUS)
        cam.set_exposure_time(float(DEFAULT_EXPOSURE_US))
        # A hardware ROI persists on the camera across sessions; start full-frame.
        self.set_full_region()

    # -- description -----------------------------------------------------------
    def description(self):
        c = self.camera
        return (f"{c.get_vendor_name()} {c.get_model_name()} "
                f"(s/n {c.get_device_serial_number()})  {self.pixel_format}")

    def exposure_bounds_us(self):
        lo, hi = self.camera.get_exposure_time_bounds()
        return int(lo), int(hi)

    def get_exposure_us(self):
        return int(self.camera.get_exposure_time())

    def set_exposure_us(self, us):
        lo, hi = self.camera.get_exposure_time_bounds()
        self.camera.set_exposure_time(float(np.clip(us, lo, hi)))

    def gain_bounds_db(self):
        lo, hi = self.camera.get_gain_bounds()
        return float(lo), float(hi)

    def get_gain_db(self):
        return float(self.camera.get_gain())

    def set_gain_db(self, db):
        lo, hi = self.camera.get_gain_bounds()
        self.camera.set_gain(float(np.clip(db, lo, hi)))

    # -- frame rate (decoupled from exposure) ----------------------------------
    def enable_manual_frame_rate(self, enable=True):
        """Flip AcquisitionFrameRateEnable so AcquisitionFrameRate is honored.

        Without this, the camera ignores AcquisitionFrameRate and runs at its
        default rate regardless of ROI -- the rate is NOT free-running at max.
        """
        dev = self.camera.get_device()
        if self.camera.is_feature_available("AcquisitionFrameRateAuto"):
            try:
                dev.set_string_feature_value("AcquisitionFrameRateAuto", "Off")
            except Exception:
                pass
        for name in ("AcquisitionFrameRateEnable", "AcquisitionFrameRateEnabled"):
            if self.camera.is_feature_available(name):
                dev.set_boolean_feature_value(name, enable)
                return name
        return None

    def frame_rate_bounds(self):
        lo, hi = self.camera.get_frame_rate_bounds()
        return float(lo), float(hi)

    def get_frame_rate(self):
        return float(self.camera.get_frame_rate())

    def set_frame_rate(self, fps):
        """Enable manual control and set the rate, clamped to the current bounds.

        Bounds depend on ROI/pixel format/exposure, so they are read after the
        enable flip (which is what makes the high end reflect the real ceiling).
        """
        self.enable_manual_frame_rate(True)
        lo, hi = self.camera.get_frame_rate_bounds()
        self.camera.set_frame_rate(float(np.clip(fps, lo, hi)))
        return self.get_frame_rate()

    # -- pixel format ----------------------------------------------------------
    def set_pixel_format(self, pixel_format):
        """Switch the sensor pixel format (e.g. "Mono8" or "Mono16").

        PixelFormat is locked while the camera streams, so the stream is stopped
        and restarted (which also reallocates buffers for the new payload).
        Updates sat_level so the contrast/saturation math matches the new bit
        depth; latest_frame() reads the dtype from pixel_format on its own.
        """
        if pixel_format == self.pixel_format:
            return
        was = self.is_running
        if was:
            self.stop()
        self.camera.set_pixel_format_from_string(pixel_format)
        self.pixel_format = pixel_format
        self.sat_level = 255 if self._is_mono8() else 65000
        if was:
            self.start()

    # -- hardware ROI (sensor region of interest) ------------------------------
    def sensor_size(self):
        w, h = self.camera.get_sensor_size()
        return int(w), int(h)

    @staticmethod
    def _snap(value, inc, lo, hi):
        snapped = int(round(value / inc)) * inc
        return int(max(lo, min(hi, snapped)))

    def set_full_region(self):
        """Read out the full sensor. Restarts the stream if it was running."""
        was = self.is_running
        if was:
            self.stop()
        cam = self.camera
        # Move offset to 0 first: the width/height maxima depend on the offset.
        _, _, w0, h0 = cam.get_region()
        cam.set_region(0, 0, w0, h0)
        _, w_max = cam.get_width_bounds()
        _, h_max = cam.get_height_bounds()
        cam.set_region(0, 0, w_max, h_max)
        if was:
            self.start()
        return cam.get_region()

    def set_roi_region(self, cx, cy, size, height=None):
        """Read out a ~`size` x ~`height` box centred near (cx, cy), in sensor px.

        `height` defaults to `size` (a square). Keeping it separate matters for
        the multi-ROI layout: a strip wide enough for N ROIs side by side reads
        out N x the pixels of one, not N^2, so the frame-rate gain survives.

        Width/height/offset are snapped to the sensor's increment rules (the
        offset bounds are only valid once the size is set, so we set size first).
        Restarts the stream because changing the region changes the payload.
        """
        was = self.is_running
        if was:
            self.stop()
        cam = self.camera
        # Reset offset to 0 so the width/height maxima reflect the full sensor.
        _, _, w0, h0 = cam.get_region()
        cam.set_region(0, 0, w0, h0)
        w = self._snap(size, cam.get_width_increment(), *cam.get_width_bounds())
        h = self._snap(size if height is None else height,
                       cam.get_height_increment(), *cam.get_height_bounds())
        cam.set_region(0, 0, w, h)   # set size first -> offset bounds become valid
        x = self._snap(cx - w // 2, cam.get_x_offset_increment(),
                       *cam.get_x_offset_bounds())
        y = self._snap(cy - h // 2, cam.get_y_offset_increment(),
                       *cam.get_y_offset_bounds())
        cam.set_region(x, y, w, h)
        if was:
            self.start()
        return cam.get_region()

    # -- streaming -------------------------------------------------------------
    @property
    def is_running(self):
        return self.stream is not None

    def start(self):
        if self.is_running:
            return
        self.stream = self.camera.create_stream(None, None)
        payload = self.camera.get_payload()
        for _ in range(self.n_buffers):
            self.stream.push_buffer(Aravis.Buffer.new_allocate(payload))
        self.camera.start_acquisition()

    def stop(self):
        if not self.is_running:
            return
        self.camera.stop_acquisition()
        self.stream = None

    def latest_frame(self):
        """Drain the stream to the newest ready buffer; return it as (H, W) ndarray.

        All popped buffers are recycled back into the stream. Returns None if no
        frame is ready yet.
        """
        if not self.is_running:
            return None
        popped = []
        while True:
            buf = self.stream.try_pop_buffer()
            if buf is None:
                break
            popped.append(buf)
        if not popped:
            return None

        newest = popped[-1]
        frame = None
        if newest.get_status() == Aravis.BufferStatus.SUCCESS:
            h = newest.get_image_height()
            w = newest.get_image_width()
            dtype = np.uint8 if self._is_mono8() else np.uint16
            # Copy out: the buffer memory is reused once we push it back.
            frame = np.frombuffer(newest.get_data(), dtype=dtype).reshape(h, w).copy()
        for buf in popped:
            self.stream.push_buffer(buf)
        return frame

    def stream_stats(self):
        """(n_completed, n_failures, n_underruns) counters from the live stream.

        These count *every* frame the camera delivered, so they give the true
        acquisition rate -- unlike the display loop, which only consumes the
        newest buffer. Counters reset to 0 each time the stream is recreated
        (e.g. on an ROI/pixel-format change). Returns zeros when not streaming.
        """
        if self.stream is None:
            return (0, 0, 0)
        try:
            n_completed, n_failures, n_underruns = self.stream.get_statistics()
            return (int(n_completed), int(n_failures), int(n_underruns))
        except Exception:
            return (0, 0, 0)


def roi_stats(frame, top, left, size, sat_level, raw=None, n_tiles=TILES_PER_AXIS):
    """ROI statistics with a *tiled* contrast.

    The contrast is computed by splitting the ROI into an n_tiles x n_tiles grid,
    taking std/mean within each tile, and averaging those per-tile contrasts.
    Averaging local contrasts removes ROI-scale illumination gradients (which
    would inflate a single global std/mean) and leaves the local speckle contrast.

    Stats use `frame` (possibly background-subtracted); saturation and max use
    `raw` (the un-subtracted sensor frame). `raw` defaults to `frame`.
    """
    roi = frame[top:top + size, left:left + size].astype(np.float64)
    raw_src = raw if raw is not None else frame
    raw_roi = raw_src[top:top + size, left:left + size].astype(np.float64)

    # Tiled contrast: per-tile std/mean, averaged over the N×N tiles.
    n = max(1, int(n_tiles))
    tile = size // n
    if tile >= 1:
        m = tile * n
        sub = roi[:m, :m].reshape(n, tile, n, tile)
        tmean = sub.mean(axis=(1, 3))
        tstd = sub.std(axis=(1, 3))
        with np.errstate(divide="ignore", invalid="ignore"):
            tcontrast = np.where(tmean > 0, tstd / tmean, np.nan)
        contrast = (float(np.nanmean(tcontrast))
                    if np.isfinite(tcontrast).any() else 0.0)
    else:
        contrast = 0.0

    mean = float(roi.mean())
    d = roi - mean
    denom = float((d * d).mean())
    acf1 = float((d[:, :-1] * d[:, 1:]).mean() / denom) if denom > 0 else 0.0
    return {
        "mean": mean,
        "std": float(roi.std()),
        "contrast": contrast,
        "max": float(raw_roi.max()),
        "sat_frac": float((raw_roi >= sat_level).mean()),
        "acf1": acf1,
        "n_tiles": n,
    }


class CameraView(Base):
    """ttk.Label showing live frames, with a click-to-move square ROI overlay."""

    def __init__(self, camera, roi_size_getter, stats_callback):
        super().__init__()
        self.camera = camera
        self.roi_size_getter = roi_size_getter   # callable -> int
        self.stats_callback = stats_callback      # callable(list of stats_dict)
        # One (cx, cy) per ROI, in *frame* pixels. None = not placed yet, so the
        # next render drops it at its default spot for the current frame size.
        self.roi_centers = [None] * N_ROIS
        self.hardware_roi = False                  # True while the sensor readout is cropped
        self.stretch = True                        # auto-stretch display brightness
        self._tkimage = None
        self._scheduled = None
        # Fixed on-screen box, sized once from the full sensor so a full-frame
        # readout fills it exactly. Frames are scaled to fit inside it.
        sw, sh = camera.sensor_size()
        self._canvas_w = int(DISPLAY_MAX_WIDTH)
        self._canvas_h = max(1, round(DISPLAY_MAX_WIDTH * sh / sw))
        # Frame -> canvas mapping (x_canvas = x_frame * k + ox), kept for clicks.
        self._k = 1.0
        self._origin = (0, 0)
        self._frame_shape = None                   # (h, w) of the last rendered frame
        self._frame_times = deque(maxlen=30)
        self.fps = 0.0
        self.dt_mean_ms = 0.0
        self.dt_std_ms = 0.0
        self.background = None                      # averaged background frame (float) or None
        self.subtract_bg = False
        self._bg_frames = None                     # accumulator while capturing background
        self._bg_target = 0
        self.on_background_captured = None          # callable(mean_value)

    def create_widget(self, master):
        self.widget = ttk.Label(master, borderwidth=2, relief="groove")
        # Blank image at the final size: the label is sized by its image, so
        # this stops it from growing when the first frame lands.
        self._tkimage = ImageTk.PhotoImage(
            Image.new("RGB", (self._canvas_w, self._canvas_h), (0, 0, 0)))
        self.widget.configure(image=self._tkimage)
        # Plain click moves ROI 1, shift-click moves ROI 2. Binding the two
        # sequences separately lets Tk dispatch on specificity, which is more
        # portable than decoding the modifier bits out of event.state.
        self.widget.bind("<Button-1>", lambda e: self._on_click(e, 0))
        self.widget.bind("<Shift-Button-1>", lambda e: self._on_click(e, 1))
        self._scheduled = App.app.root.after(REFRESH_MS, self.update_display)

    # -- ROI geometry ----------------------------------------------------------
    def _default_center(self, index, h, w):
        """Spread the ROIs evenly across the frame width, on the mid line."""
        return ((2 * index + 1) * w // (2 * N_ROIS), h // 2)

    def reset_roi_centers(self):
        """Forget ROI placement; the next render re-drops them for the frame.

        Called whenever the sensor region changes: the stored centres are in
        frame pixels, and a crop changes what a frame pixel means.
        """
        self.roi_centers = [None] * N_ROIS

    def _roi_box(self, h, w, index):
        """(top, left, size) of ROI `index`, clipped inside an h x w frame.

        Both ROIs are plain software boxes, including while a hardware ROI is
        engaged -- the cropped readout is just a smaller frame to place them in.
        """
        size = int(np.clip(self.roi_size_getter(), 8, min(h, w)))
        if self.roi_centers[index] is None:
            self.roi_centers[index] = self._default_center(index, h, w)
        cx, cy = self.roi_centers[index]
        left = int(np.clip(cx - size // 2, 0, w - size))
        top = int(np.clip(cy - size // 2, 0, h - size))
        return top, left, size

    def _on_click(self, event, index=0):
        """Move ROI `index` to the click, mapping display pixels to frame pixels."""
        # Use the shape recorded by the last _render: latest_frame() drains the
        # stream and returns None when no new buffer is ready, so calling it
        # here would drop most clicks and steal frames from the display loop.
        if not self._k or self._frame_shape is None:
            return
        ox, oy = self._origin
        h, w = self._frame_shape
        cx = int(np.clip((event.x - ox) / self._k, 0, w - 1))
        cy = int(np.clip((event.y - oy) / self._k, 0, h - 1))
        self.roi_centers[index] = (cx, cy)

    def capture_background(self, n=16):
        """Begin averaging n frames into a background frame. Block the beam first."""
        self._bg_frames = []
        self._bg_target = max(1, int(n))

    # -- refresh loop ----------------------------------------------------------
    def update_display(self):
        frame = self.camera.latest_frame()
        if frame is not None:
            if self._bg_frames is not None:
                self._bg_frames.append(frame.astype(np.float64))
                if len(self._bg_frames) >= self._bg_target:
                    self.background = np.mean(self._bg_frames, axis=0)
                    self._bg_frames = None
                    if self.on_background_captured is not None:
                        self.on_background_captured(float(self.background.mean()))
            self._render(frame)
            self._frame_times.append(time.monotonic())
            if len(self._frame_times) > 1:
                dts = np.diff(np.asarray(self._frame_times))   # seconds
                mean_dt = float(dts.mean())
                self.fps = 1.0 / mean_dt if mean_dt > 0 else 0.0
                self.dt_mean_ms = mean_dt * 1000.0
                self.dt_std_ms = float(dts.std()) * 1000.0
        self._scheduled = App.app.root.after(REFRESH_MS, self.update_display)

    def _render(self, frame):
        h, w = frame.shape
        self._frame_shape = (h, w)
        bg_on = (self.subtract_bg and self.background is not None
                 and self.background.shape == frame.shape)
        work = (frame.astype(np.float64) - self.background) if bg_on else frame

        # One reduction per ROI. Two 300px ROIs is a negligible cost next to the
        # resize below; the shared display work is done once, further down.
        boxes = [self._roi_box(h, w, i) for i in range(N_ROIS)]
        all_stats = []
        for i, (top, left, size) in enumerate(boxes):
            st = roi_stats(work, top, left, size, self.camera.sat_level, raw=frame)
            st.update(index=i, roi=(top, left, size), fps=self.fps,
                      bg_subtracted=bg_on, dt_mean_ms=self.dt_mean_ms,
                      dt_std_ms=self.dt_std_ms)
            all_stats.append(st)
        if self.stats_callback:
            self.stats_callback(all_stats)

        # Cheap integer subsample first: it costs nothing and keeps the resize
        # below working on a small array. Never subsamples past the canvas size.
        step = max(1, w // self._canvas_w, h // self._canvas_h)
        small = work[::step, ::step].astype(np.float64)
        if bg_on:
            small = np.clip(small, 0, None)

        # Stretch on the subsampled frame, BEFORE padding: percentiles taken
        # over the black margins would wash out a small ROI.
        if self.stretch:
            lo, hi = np.percentile(small, [0.5, 99.5])
            if hi <= lo:
                hi = lo + 1
        else:
            lo, hi = 0, self.camera.sat_level
        disp = np.clip((small - lo) / (hi - lo) * 255, 0, 255)
        img = Image.fromarray(disp.astype(np.uint8)).convert("RGB")

        # Scale to fit the fixed canvas, preserving aspect ratio, and centre it.
        # NEAREST on purpose: interpolation would smooth the speckle grain and
        # make the acf1 grain-size gauge lie about what the sensor sees.
        sh_, sw_ = small.shape
        scale = min(self._canvas_w / sw_, self._canvas_h / sh_)
        new_w = max(1, round(sw_ * scale))
        new_h = max(1, round(sh_ * scale))
        img = img.resize((new_w, new_h), Image.NEAREST)

        canvas = Image.new("RGB", (self._canvas_w, self._canvas_h), (0, 0, 0))
        ox = (self._canvas_w - new_w) // 2
        oy = (self._canvas_h - new_h) // 2
        canvas.paste(img, (ox, oy))

        # frame pixels -> canvas pixels, for the overlay and for _on_click.
        k = scale / step
        self._k = k
        self._origin = (ox, oy)

        draw = ImageDraw.Draw(canvas)
        for i, (top, left, size) in enumerate(boxes):
            x0, y0 = round(left * k) + ox, round(top * k) + oy
            x1, y1 = round((left + size) * k) + ox, round((top + size) * k) + oy
            draw.rectangle([x0, y0, x1, y1], outline=ROI_COLORS[i], width=2)
            draw.text((x0 + 4, y0 + 2), str(i + 1), fill=ROI_COLORS[i])
        self._tkimage = ImageTk.PhotoImage(canvas)
        self.widget.configure(image=self._tkimage)


def _hex(rgb):
    """(r, g, b) 0-255 -> '#rrggbb', so plot and overlay share one definition."""
    return "#%02x%02x%02x" % tuple(int(c) for c in rgb)


class ContrastPlot(XYPlot):
    """Contrast curves on the left axis, their ratio on a right-hand axis.

    `series` is a list of (x, y, color, label) drawn against contrast on the
    left. `ratio_series`, if set, is a single (x, y, color, label) drawn against
    its own scale on the right: a ratio lives around 1 while contrasts sit
    around 0.1-0.5, so sharing one axis would flatten both.

    update_plot() clears the axes each redraw, so the formatters and legend are
    re-applied every time rather than once at setup. The twin axis is created
    once and reused -- making a new one per redraw would stack them up.
    """

    def __init__(self, figsize):
        super().__init__(figsize=figsize)
        self.series = []
        self.ratio_series = None
        self._ratio_axis = None

    def update_plot(self):
        ax = self.first_axis
        ax.clear()
        handles = []
        for x, y, color, label in self.series:
            handles += ax.plot(x, y, "-", color=color, label=label)
        ax.yaxis.set_major_formatter(FormatStrFormatter("%.2f"))

        legend_axis = ax
        if self.ratio_series is not None:
            if self._ratio_axis is None:
                self._ratio_axis = ax.twinx()
            rax = self._ratio_axis
            rax.clear()
            x, y, color, label = self.ratio_series
            handles += rax.plot(x, y, "--", color=color, label=label)
            rax.yaxis.set_major_formatter(FormatStrFormatter("%.2f"))
            rax.tick_params(axis="y", labelcolor=color, labelsize="x-small")
            # twinx draws the twin on top, so the legend goes there or the
            # ratio line would be drawn over it.
            legend_axis = rax

        if handles:
            legend_axis.legend(handles, [h.get_label() for h in handles],
                               loc="upper right", fontsize="x-small",
                               framealpha=0.6)
        self.figure.canvas.draw()
        self.figure.canvas.flush_events()


class SpeckleViewerApp(App):
    def __init__(self):
        super().__init__(name="Speckle Viewer")
        self.window.widget.title("Speckle Viewer — live ROI contrast")
        self.window.widget.protocol("WM_DELETE_WINDOW", self.quit)

        self.camera = SpeckleCamera()
        exp_lo, exp_hi = self.camera.exposure_bounds_us()

        # --- controls (left) --------------------------------------------------
        self.controls = Box(label="Controls")
        self.controls.grid_into(self.window, row=0, column=0, padx=10, pady=10,
                                sticky="nsew")

        # --- image (right) ----------------------------------------------------
        self.view = CameraView(
            self.camera,
            roi_size_getter=lambda: self.roi_size_control.value,
            stats_callback=self.on_stats,
        )
        self.view.grid_into(self.window, row=0, column=1, padx=10, pady=10,
                            sticky="nw")

        self.start_button = Button("Stop", user_event_callback=self.toggle_run)
        self.start_button.grid_into(self.controls, row=0, column=0, padx=8, pady=6,
                                    sticky="w")
        Button("Center ROI", user_event_callback=self.center_roi).grid_into(
            self.controls, row=0, column=1, padx=8, pady=6, sticky="w")
        Button("Save frame…", user_event_callback=self.save_frame).grid_into(
            self.controls, row=0, column=2, padx=8, pady=6, sticky="w")

        Label("ROI size (px):").grid_into(self.controls, row=1, column=0,
                                          padx=8, pady=6, sticky="e")
        self.roi_size_control = IntEntry(value=DEFAULT_ROI_SIZE, width=7,
                                         minimum=8, maximum=2048)
        self.roi_size_control.grid_into(self.controls, row=1, column=1, padx=8,
                                        pady=6, sticky="w")
        self.hw_roi_box = Checkbox(label="Hardware ROI",
                                   user_callback=self.toggle_hardware_roi)
        self.hw_roi_box.grid_into(self.controls, row=1, column=2, padx=8, pady=6,
                                  sticky="w")
        self.hw_roi_box.value = False

        Label("Exposure (µs):").grid_into(self.controls, row=2, column=0,
                                          padx=8, pady=6, sticky="e")
        self.exposure_control = IntEntry(value=self.camera.get_exposure_us(),
                                         width=10, minimum=exp_lo, maximum=exp_hi)
        self.exposure_control.grid_into(self.controls, row=2, column=1, padx=8,
                                        pady=6, sticky="w")
        self._applied_exposure = self.camera.get_exposure_us()

        gain_lo, gain_hi = self.camera.gain_bounds_db()
        Label(f"Gain ({gain_lo:.0f}–{gain_hi:.0f} dB):").grid_into(
            self.controls, row=3, column=0, padx=8, pady=6, sticky="e")
        self.gain_control = FormattedEntry(value=self.camera.get_gain_db(),
                                           character_width=8,
                                           format_string="{0:.2f}")
        self.gain_control.grid_into(self.controls, row=3, column=1, padx=8,
                                    pady=6, sticky="w")
        self._applied_gain = self.camera.get_gain_db()

        Label("Frame rate (fps):").grid_into(self.controls, row=4, column=0,
                                             padx=8, pady=6, sticky="e")
        self.framerate_control = FormattedEntry(value=self.camera.get_frame_rate(),
                                                character_width=8,
                                                format_string="{0:.1f}")
        self.framerate_control.grid_into(self.controls, row=4, column=1, padx=8,
                                         pady=6, sticky="w")
        self._applied_fps = self.camera.get_frame_rate()

        Label("Pixel format:").grid_into(self.controls, row=5, column=0,
                                         padx=8, pady=6, sticky="e")
        self.pixel_format_menu = PopupMenu(["Mono8", "Mono16"],
                                           user_callback=self.on_pixel_format)
        self.pixel_format_menu.grid_into(self.controls, row=5, column=1, padx=8,
                                         pady=6, sticky="w")
        self.pixel_format_menu.value = self.camera.pixel_format

        self.stretch_box = Checkbox(label="Auto-stretch display",
                                    user_callback=self.toggle_stretch)
        self.stretch_box.grid_into(self.controls, row=6, column=0, columnspan=2,
                                   padx=8, pady=6, sticky="w")

        # --- background subtraction ------------------------------------------
        Button("Capture background", user_event_callback=self.capture_background
               ).grid_into(self.controls, row=7, column=0, padx=8, pady=6,
                           sticky="w")
        self.subtract_box = Checkbox(label="Subtract background",
                                     user_callback=self.toggle_subtract)
        self.subtract_box.grid_into(self.controls, row=7, column=1, columnspan=2,
                                    padx=8, pady=6, sticky="w")
        self.subtract_box.value = False
        self.bg_label = Label("background: not captured")
        self.bg_label.grid_into(self.controls, row=8, column=0, columnspan=3,
                                padx=8, pady=2, sticky="w")
        self.view.on_background_captured = self.on_background_captured

        # --- live readout ------------------------------------------------------
        # One column per ROI: field name in column 0, ROI i's value in column
        # i+1. self.roi_labels[i][field] is the Label that on_stats writes.
        self.readout = Box(label="ROI", width=520, height=250)
        self.readout.grid_into(self.controls, row=9, column=0, columnspan=3,
                               padx=8, pady=8, sticky="nsew")
        fields = [("contrast", "contrast:"), ("mean", "mean:"),
                  ("max", "max:"), ("sat", "saturated:"),
                  ("acf1", "grain acf1:"), ("roi", "at:")]
        for i in range(N_ROIS):
            Label(f"ROI {i + 1}").grid_into(self.readout, row=0, column=i + 1,
                                            padx=8, pady=2, sticky="w")
        self.roi_labels = [{} for _ in range(N_ROIS)]
        for r, (key, caption) in enumerate(fields, start=1):
            Label(caption).grid_into(self.readout, row=r, column=0, padx=8,
                                     pady=2, sticky="e")
            for i in range(N_ROIS):
                lab = Label("—")
                lab.grid_into(self.readout, row=r, column=i + 1, padx=8, pady=2,
                              sticky="w")
                self.roi_labels[i][key] = lab
        self.ratio_label = Label("contrast ROI 1 / ROI 2: —")
        self.fps_label = Label("fps: —")
        self.dt_label = Label("frame Δt: —")
        for r, lab in enumerate([self.ratio_label, self.fps_label, self.dt_label],
                                start=len(fields) + 1):
            lab.grid_into(self.readout, row=r, column=0, columnspan=N_ROIS + 1,
                          padx=8, pady=2, sticky="w")

        # --- rolling contrast plot -------------------------------------------
        # Redrawing the plot every tick is the most expensive UI step; the
        # checkbox lets you switch it off to keep the live image responsive.
        self.update_plot_box = Checkbox(label="Update contrast plot")
        self.update_plot_box.grid_into(self.controls, row=10, column=0,
                                       columnspan=3, padx=8, pady=6, sticky="w")
        self.update_plot_box.value = True
        self.plot = ContrastPlot(figsize=(3.6, 1.8))
        self.plot.grid_into(self.controls, row=11, column=0, columnspan=3,
                            padx=8, pady=8, sticky="nsew")
        self.history = deque()   # (t, [contrast per ROI])
        self._t0 = time.monotonic()

        # True acquisition rate is derived in slow_tick from the stream counters
        # (the display loop only samples the newest frame ~33x/s, so its rate is
        # not the camera's). Track the previous (completed, dropped, time).
        self._stream_prev = (0, 0, time.monotonic())
        self._acq_fps = 0.0
        self._acq_drop_rate = 0.0

        self.camera.start()
        self._install_signal_handlers()
        self.after(SLOW_MS, self.slow_tick)

    # -- callbacks -------------------------------------------------------------
    def on_stats(self, stats):
        """Fill the readout columns. `stats` is one dict per ROI, in order."""
        for i, s in enumerate(stats):
            tag = "  (bg-sub)" if s.get("bg_subtracted") else ""
            lab = self.roi_labels[i]
            lab["contrast"].text = f"{s['contrast']:.4f}{tag}"
            lab["mean"].text = f"{s['mean']:.0f}"
            lab["max"].text = f"{s['max']:.0f}"
            lab["sat"].text = f"{100 * s['sat_frac']:.2f}%"
            hint = ("~1px undersampled" if s["acf1"] < 0.2
                    else "~1.5px" if s["acf1"] < 0.4 else "~2px ok")
            lab["acf1"].text = f"{s['acf1']:.3f} ({hint})"
            t, l, sz = s["roi"]
            nt = s.get("n_tiles", 1)
            lab["roi"].text = f"{sz}px (row {t}, col {l})  {nt}×{nt}"
        # Contrast ratio between the two ROIs. Undefined when the denominator
        # is zero (a dark or fully saturated ROI 2 gives contrast 0).
        if len(stats) >= 2:
            c1, c2 = stats[0]["contrast"], stats[1]["contrast"]
            ratio = f"{c1 / c2:.3f}" if c2 > 0 else "—"
            self.ratio_label.text = f"contrast ROI 1 / ROI 2: {ratio}"
        # fps (true acquisition rate) is owned by slow_tick via the stream
        # counters; here we report only the display refresh interval, so the two
        # numbers aren't confused (display is capped at ~1000/REFRESH_MS fps).
        if stats:
            self.dt_label.text = (f"display Δt: {stats[0]['dt_mean_ms']:.1f} ± "
                                  f"{stats[0]['dt_std_ms']:.1f} ms")
        self.history.append((time.monotonic() - self._t0,
                             [s["contrast"] for s in stats]))

    def slow_tick(self):
        # Apply exposure changes typed into the entry. Store the *requested*
        # value (not the camera's quantized readback) so we don't re-send the
        # same exposure every tick -- re-applying mid-stream glitches frames.
        try:
            wanted = int(self.exposure_control.value)
            if wanted != self._applied_exposure:
                self.camera.set_exposure_us(wanted)
                self._applied_exposure = wanted
        except Exception:
            pass

        # Apply gain changes typed into the entry (same requested-value rule).
        try:
            wanted_gain = float(self.gain_control.value)
            if abs(wanted_gain - self._applied_gain) > 1e-6:
                self.camera.set_gain_db(wanted_gain)
                self._applied_gain = wanted_gain
        except Exception:
            pass

        # Apply frame-rate changes: enable manual control + set AcquisitionFrameRate.
        try:
            wanted_fps = float(self.framerate_control.value)
            if abs(wanted_fps - self._applied_fps) > 1e-3:
                self.camera.set_frame_rate(wanted_fps)
                self._applied_fps = wanted_fps
        except Exception:
            pass

        # Report the *true* acquisition rate from the stream counters. The
        # display loop only consumes the newest frame ~33x/s (REFRESH_MS), so
        # its rate is not the camera's; this counts every delivered frame.
        try:
            n_done, n_fail, n_under = self.camera.stream_stats()
            n_drop = n_fail + n_under
            t = time.monotonic()
            p_done, p_drop, p_t = self._stream_prev
            dn, dd, dt = n_done - p_done, n_drop - p_drop, t - p_t
            if dn < 0:                       # stream was recreated -> counters reset
                dn, dd = n_done, n_drop
            if dt > 0:
                self._acq_fps = dn / dt
                self._acq_drop_rate = max(dd, 0) / dt
            self._stream_prev = (n_done, n_drop, t)
            drop = (f"  (−{self._acq_drop_rate:.0f}/s dropped)"
                    if self._acq_drop_rate > 0.5 else "")
            self.fps_label.text = f"fps: {self._acq_fps:.1f}{drop}"
        except Exception:
            pass

        # Keep the history pruned regardless, so the plot is current whenever
        # it's shown, but skip the (expensive) redraw when the box is off.
        now = time.monotonic() - self._t0
        while self.history and now - self.history[0][0] > PLOT_HISTORY_S:
            self.history.popleft()
        if self.update_plot_box.value and len(self.history) > 1:
            times = [t for t, _ in self.history]
            self.plot.series = [
                (times, [cs[i] for _, cs in self.history],
                 _hex(ROI_COLORS[i]), f"ROI {i + 1}")
                for i in range(N_ROIS)
            ]
            # NaN where ROI 2's contrast is zero: matplotlib breaks the line
            # there instead of drawing an infinity.
            ratios = [cs[0] / cs[1] if len(cs) > 1 and cs[1] > 0 else float("nan")
                      for _, cs in self.history]
            self.plot.ratio_series = (times, ratios, RATIO_COLOR, "ratio 1/2")
            self.plot.update_plot()

        if self.is_running:
            self.after(SLOW_MS, self.slow_tick)

    def toggle_run(self, event, button):
        if self.camera.is_running:
            self.camera.stop()
            button.label = "Start"
        else:
            self.camera.start()
            button.label = "Stop"

    def center_roi(self, event, button):
        # Both ROIs are re-dropped at their default spread next render.
        self.view.reset_roi_centers()

    def toggle_stretch(self, checkbox):
        self.view.stretch = bool(checkbox.value)

    def on_pixel_format(self, menu, index):
        fmt = menu.value
        try:
            self.camera.set_pixel_format(fmt)
        except Exception as exc:
            print(f"Could not set pixel format to {fmt}: {exc}")
            return
        # Bit depth changed, so any captured background (old depth) no longer applies.
        self.view.background = None
        self.bg_label.text = f"background: re-capture (pixel format now {fmt})"

    def toggle_subtract(self, checkbox):
        self.view.subtract_bg = bool(checkbox.value)
        if self.view.subtract_bg and self.view.background is None:
            self.bg_label.text = "background: capture one first!"

    def toggle_hardware_roi(self, checkbox):
        if bool(checkbox.value):
            sw, sh = self.camera.sensor_size()
            # Engaged from a full-frame readout, so ROI 1's centre is in sensor
            # pixels here. Give the crop some room: the two software ROIs have
            # to fit side by side inside it.
            cx, cy = self.view.roi_centers[0] or (sw // 2, sh // 2)
            one = int(self.roi_size_control.value)
            # A strip N ROIs wide but only one ROI tall: enough room to lay them
            # out side by side, without paying N^2 pixels for it.
            region = self.camera.set_roi_region(cx, cy, one * N_ROIS, height=one)
            self.view.hardware_roi = True
        else:
            region = self.camera.set_full_region()
            self.view.hardware_roi = False

        # A region change moves the achievable frame-rate ceiling, but the camera
        # keeps running at the previously pinned AcquisitionFrameRate until a new
        # rate is pushed. Re-apply so a small ROI actually runs fast: ride the new
        # ceiling up when an ROI is engaged, drop back under the (lower) full-frame
        # ceiling when it is released. Keep the field and _applied_fps in sync so
        # slow_tick doesn't immediately re-send a stale value.
        try:
            _, hi = self.camera.frame_rate_bounds()
            target = hi if self.view.hardware_roi else float(self.framerate_control.value)
            achieved = self.camera.set_frame_rate(target)
            self.framerate_control.value = achieved
            self._applied_fps = float(self.framerate_control.value)
        except Exception as exc:
            print(f"Could not re-apply frame rate after ROI change: {exc}")

        # Region change resizes the frame, so any captured background no longer
        # fits -- and the stored ROI centres are frame pixels, which a crop
        # redefines. Drop both so they land inside the new frame.
        self.view.background = None
        self.view.reset_roi_centers()
        x, y, w, h = region
        self.bg_label.text = f"background: re-capture (region now {w}×{h} @ {x},{y})"

    def capture_background(self, event, button):
        self.bg_label.text = "background: capturing… (block the beam)"
        self.view.capture_background(16)

    def on_background_captured(self, value):
        self.bg_label.text = f"background: {value:.1f} DN (avg of 16 frames)"

    def save_frame(self, event, button):
        frame = self.camera.latest_frame()
        if frame is None:
            return
        path = filedialog.asksaveasfilename(
            title="Save current frame", defaultextension=".png",
            filetypes=[("PNG", ".png")])
        if path:
            Image.fromarray(frame).save(path)

    # App menu hooks (avoid NotImplementedError from the default menu).
    def save(self):
        self.save_frame(None, None)

    def preferences(self):
        pass

    def _install_signal_handlers(self):
        """Release the camera on SIGINT/SIGTERM, not just on window close.

        Without this a plain `kill` (or Ctrl-C) leaves the sensor acquiring, and
        the next launch dies on the PixelFormat write. Tk's mainloop blocks in
        C, so Python runs the handler on its next callback into Python -- the
        display loop ticks every REFRESH_MS, so the delay is imperceptible.
        """
        def handler(sig, frame):
            self.quit()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handler)
            except ValueError:
                pass          # not the main thread: nothing to install

    def quit(self):
        try:
            self.camera.stop()
        except Exception:
            pass
        super().quit()


if __name__ == "__main__":
    app = SpeckleViewerApp()
    app.mainloop()
