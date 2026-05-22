#!/usr/bin/env python3
"""
Capture an exposure sweep from an Aravis-compatible GenICam camera
(e.g. FLIR Blackfly) and save each frame to disk.

Edit the CONFIG block below, then run:
    python3 take_exposure_sweep.py
"""

# --- macOS / Homebrew env bootstrap ------------------------------------------
# Homebrew puts PyGObject and Aravis under /opt/homebrew. A python.org Python
# won't find them without DYLD_FALLBACK_LIBRARY_PATH / PYTHONPATH /
# GI_TYPELIB_PATH set BEFORE the process starts. Re-exec self once with those
# vars so the user can just `python3 take_exposure_sweep.py`. Harmless when
# already using Homebrew's Python.
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

from pathlib import Path

import numpy as np
from PIL import Image

import gi
gi.require_version("Aravis", "0.8")
from gi.repository import Aravis


# =============================================================================
# CONFIG -- edit to suit your experiment
# =============================================================================

# None  = open the first camera found.
# str   = device ID, vendor-model-serial, or user-defined name (see arv-tool-0.8)
CAMERA_ID = None

# Exposure times in microseconds (decade-stepped):
#   10 .. 100 us       step 10
#   100 .. 1000 us     step 100
#   1000 .. 10000 us   step 1000
#   10000 .. 100000 us step 10000
EXPOSURE_TIMES_US = (
    list(range(10, 101, 10))
    + list(range(200, 1001, 100))
    + list(range(2000, 10001, 1000))
    + list(range(20000, 100001, 10000))
)

# Camera pixel format. Common FLIR mono options: "Mono8", "Mono12", "Mono16".
PIXEL_FORMAT = "Mono16"

# Where to write the PNGs.
OUTPUT_DIR = Path("captures_2026-05-21_dim")

# Number of frames to capture at each exposure. analyze_sweep.py averages the
# per-frame contrast over these repeats and reports the spread across them.
FRAMES_PER_EXPOSURE = 10

# Per-frame timeout. Must exceed the longest exposure you ask for.
TIMEOUT_S = 5.0

# Stop the sweep when a frame's brightest pixel exceeds this value.
# Lets the user attenuate the source before pushing every long exposure
# into the 16-bit ceiling (65408 for the IMX178's 12-bit ADC, left-shifted).
SATURATION_STOP = 60000

# =============================================================================


def open_camera(camera_id):
    Aravis.update_device_list()
    n = Aravis.get_n_devices()
    if n == 0:
        sys.exit("No Aravis-compatible cameras detected. "
                 "Try `arv-tool-0.8` to check discovery.")
    if camera_id is None:
        camera_id = Aravis.get_device_id(0)
    print(f"Opening camera: {camera_id}")
    return Aravis.Camera.new(camera_id)


def configure(camera, pixel_format):
    # Disable auto-exposure so our manually-set exposure sticks.
    camera.set_exposure_time_auto(Aravis.Auto.OFF)

    # Force gain to the camera's minimum (0 dB on FLIR Blackfly S). We turn
    # auto-gain off first, then pin the value -- otherwise the auto loop can
    # override whatever we set.
    try:
        camera.set_gain_auto(Aravis.Auto.OFF)
        gain_min, _ = camera.get_gain_bounds()
        camera.set_gain(gain_min)
        print(f"Gain pinned to {gain_min:.2f} (camera minimum)")
    except Exception as exc:
        print(f"Warning: could not force gain to minimum: {exc}")

    # Disable the camera's gamma curve so output is linear in photon count.
    if camera.is_feature_available("GammaEnable"):
        camera.set_boolean("GammaEnable", False)
        print("GammaEnable set to False (linear output)")

    camera.set_pixel_format_from_string(pixel_format)

    # Leave the ROI at the camera's power-on default (full frame). Calling
    # set_region with the raw sensor size can violate USB3-Vision width/height
    # increment rules on this sensor.

    camera.set_acquisition_mode(Aravis.AcquisitionMode.CONTINUOUS)


def buffer_to_array(buffer):
    """Copy an Aravis buffer's pixel data into a numpy array (H, W)."""
    status = buffer.get_status()
    if status != Aravis.BufferStatus.SUCCESS:
        raise RuntimeError(f"Buffer status not SUCCESS: {status}")

    width = buffer.get_image_width()
    height = buffer.get_image_height()
    fmt = buffer.get_image_pixel_format()
    data = buffer.get_data()

    if fmt == Aravis.PIXEL_FORMAT_MONO_8:
        return np.frombuffer(data, dtype=np.uint8).reshape(height, width)

    # 10/12/14-bit data comes packed in 16-bit containers, left-justified
    # or right-justified depending on the camera. Most FLIR models use
    # right-justified, so the raw uint16 values can be used directly.
    sixteen_bit = (
        Aravis.PIXEL_FORMAT_MONO_10,
        Aravis.PIXEL_FORMAT_MONO_12,
        Aravis.PIXEL_FORMAT_MONO_14,
        Aravis.PIXEL_FORMAT_MONO_16,
    )
    if fmt in sixteen_bit:
        return np.frombuffer(data, dtype=np.uint16).reshape(height, width)

    raise ValueError(f"Unsupported pixel format: 0x{fmt:08x}")


def save_png(image, path):
    # Pillow picks mode 'L' for uint8 and 'I;16' for uint16 automatically,
    # both of which round-trip cleanly through PNG.
    Image.fromarray(image).save(path)


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    camera = open_camera(CAMERA_ID)
    print(f"Connected: {camera.get_vendor_name()} {camera.get_model_name()} "
          f"(s/n {camera.get_device_serial_number()})")

    configure(camera, PIXEL_FORMAT)

    exp_min, exp_max = camera.get_exposure_time_bounds()
    print(f"Exposure bounds: {exp_min:.0f} us .. {exp_max:.0f} us")
    print(f"Saving {len(EXPOSURE_TIMES_US)} frames to {OUTPUT_DIR}/")

    timeout_us = int(TIMEOUT_S * 1_000_000)

    for i, requested in enumerate(EXPOSURE_TIMES_US):
        # Clamp to what the camera actually supports; out-of-range values
        # would otherwise be silently coerced or rejected.
        exposure = float(np.clip(requested, exp_min, exp_max))
        camera.set_exposure_time(exposure)

        # Grab FRAMES_PER_EXPOSURE frames at this exposure. Camera.acquisition()
        # is Aravis's one-shot helper (start stream, grab one buffer, tear down);
        # we just call it repeatedly. Plenty fast for a low-rate sweep like this.
        peak = 0
        saved = 0
        for r in range(FRAMES_PER_EXPOSURE):
            buffer = camera.acquisition(timeout_us)
            if buffer is None:
                continue
            image = buffer_to_array(buffer)
            out = OUTPUT_DIR / f"frame_{i:02d}_{int(exposure):010d}us_rep{r:02d}.png"
            save_png(image, out)
            saved += 1
            peak = max(peak, int(image.max()))

        if saved == 0:
            print(f"  [{i:02d}] {exposure:>10.1f} us  -> all "
                  f"{FRAMES_PER_EXPOSURE} reps TIMED OUT, skipped")
            continue

        print(f"  [{i:02d}] {exposure:>10.1f} us  -> {saved} frames "
              f"(peak max={peak})")

        if peak >= SATURATION_STOP:
            print()
            print(f">>> Frame peak max {peak} >= SATURATION_STOP={SATURATION_STOP}.")
            print(f">>> Stopping the sweep at {exposure:.1f} us.")
            print(">>> Attenuate the laser, then re-run the script.")
            break


if __name__ == "__main__":
    main()
