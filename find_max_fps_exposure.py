"""Find the operating point for a near-gapless high-speed acquisition.

Goal: the highest frame rate just under 1000 fps with the LONGEST exposure
that still sustains it, so the exposure is ~ 1/fps and integration is nearly
continuous (minimal dead time between frames -- what you want for studying
decorrelation by summing frames).

Method, using the project's SpeckleCamera:
  1. Engage a small hardware ROI (128x128 Mono8) to reach the ~1000 fps ceiling
     (it's the row count that limits the rate; below ~128 rows it's firmware-
     capped at 1000 fps, so 128x128 is the sweet spot).
  2. For each candidate rate just under 1000 fps: pin the rate, then binary-
     search the exposure upward, watching AcquisitionResultingFrameRate (the
     *true* rate -- the exposure bound itself does NOT shrink with frame rate
     on this camera, and the AcquisitionFrameRate setpoint keeps reading the
     target even when a long exposure has actually dropped the real rate). The
     longest exposure that keeps the resulting rate at the target is the answer;
     the leftover gap is the sensor's irreducible readout dead time.
  3. Stream ~2 s and confirm the rate actually holds with zero dropped frames
     (measured from the Aravis stream counters, not the display loop).

Prints a table and recommends the longest-exposure point that holds its rate
with no drops. Read-only w.r.t. your code; leaves the camera stopped.
"""
import time

from speckle_viewer import SpeckleCamera

ROI = 128                       # px; 128x128 reaches the ceiling, smaller doesn't help
TARGETS = (995, 990, 975, 950)  # candidate frame rates, all just under 1000 fps
MEASURE_S = 1.5


def resulting_fps(cam):
    """The camera's TRUE rate given the current exposure (not the setpoint)."""
    return cam.camera.get_device().get_float_feature_value(
        "AcquisitionResultingFrameRate")


def longest_exposure_for(cam, rate_fps):
    """Binary-search the longest exposure (us) that still holds the full
    rate_fps (i.e. without the long exposure dropping the resulting rate).
    Assumes the frame rate is already pinned to rate_fps."""
    lo, _ = cam.exposure_bounds_us()            # shortest exposure -> rate not limited
    hi = int(1e6 / rate_fps)                    # one frame period; exposure can't exceed it
    while hi - lo > 1:
        mid = (lo + hi) // 2
        cam.set_exposure_us(mid)
        if resulting_fps(cam) >= rate_fps - 1.0:  # full rate still held (1 fps slack)
            lo = mid                            # exposure can grow
        else:
            hi = mid                            # too long -> rate dropped
    cam.set_exposure_us(lo)
    return cam.get_exposure_us()


def measure_stream(cam, seconds=MEASURE_S):
    """Stream briefly; return (measured_fps, dropped_frames) from stream counters."""
    cam.start()
    warm = time.monotonic() + 0.4
    while time.monotonic() < warm:           # warm up, recycling buffers
        cam.latest_frame()
        time.sleep(0.003)
    c0, f0, u0 = cam.stream_stats()
    t0 = time.monotonic()
    end = t0 + seconds
    while time.monotonic() < end:            # drain + recycle so nothing starves
        cam.latest_frame()
        time.sleep(0.002)
    c1, f1, u1 = cam.stream_stats()
    elapsed = time.monotonic() - t0
    cam.stop()
    return (c1 - c0) / elapsed, (f1 - f0) + (u1 - u0)


def main():
    cam = SpeckleCamera(pixel_format="Mono8")
    sw, sh = cam.sensor_size()
    cam.set_roi_region(sw // 2, sh // 2, ROI)
    _, _, rw, rh = cam.camera.get_region()
    print(cam.description())
    print(f"ROI {rw}x{rh}\n")

    # Minimum exposure removes exposure as a limiter so we can read the ceiling.
    cam.set_exposure_us(cam.exposure_bounds_us()[0])
    cap = cam.set_frame_rate(100000.0)
    print(f"Frame-rate ceiling at this ROI: {cap:.1f} fps  "
          f"(min period {1e6 / cap:.0f} us)\n")

    header = (f"{'target':>7} {'set':>7} {'max exp':>9} {'period':>8} "
              f"{'gap':>7} {'duty':>6} {'measured':>9} {'dropped':>8}")
    print(header)
    print("-" * len(header))

    best = None
    for target in TARGETS:
        if target > cap:
            continue
        # Reset to min exposure first so the rate can be pinned freely, then find
        # the longest exposure that keeps the true (resulting) rate at target.
        cam.set_exposure_us(cam.exposure_bounds_us()[0])
        cam.set_frame_rate(float(target))
        set_fps = cam.get_frame_rate()                  # actual rate (target gets clamped)
        applied_exp = longest_exposure_for(cam, set_fps)
        period = 1e6 / set_fps
        gap = period - applied_exp
        duty = applied_exp / period
        meas, dropped = measure_stream(cam)

        print(f"{target:7.0f} {set_fps:7.1f} {applied_exp:8d}u {period:7.0f}u "
              f"{gap:6.0f}u {duty:5.1%} {meas:9.1f} {dropped:8d}")

        # "works" = the measured rate held (within 2% measurement slack) with no
        # drops. Prefer the highest such rate (max fps); its exposure is already
        # the longest that holds that rate.
        works = dropped == 0 and meas >= 0.98 * set_fps
        if works and (best is None or meas > best["meas"]):
            best = dict(fps=set_fps, exp=applied_exp, gap=gap, duty=duty, meas=meas)

    print()
    if best:
        print(f"==> Use {best['fps']:.0f} fps with {best['exp']} us exposure: "
              f"gap {best['gap']:.0f} us ({best['duty']:.0%} duty), "
              f"measured {best['meas']:.0f} fps, 0 dropped.")
    else:
        print("No candidate held its rate with zero drops -- widen TARGETS.")

    if cam.is_running:
        cam.stop()


if __name__ == "__main__":
    main()
