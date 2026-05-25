"""Cheap FFT phase-correlation registration, to align speckle frames before
summing them into a synthetic long exposure.

Minute vibrations rigidly shift the whole speckle pattern between frames; summing
unaligned frames smears the speckle and fakes decorrelation. Phase correlation
estimates each frame's shift to sub-pixel accuracy in O(N log N), and the frame
is realigned by a Fourier shift (no interpolation blur). Pure numpy/scipy;
~3 FFTs per frame (a few seconds for 12000 frames at 128x128).

Default reference is the first frame: works cleanly when frames stay correlated
(static / slowly-decorrelating samples -- exactly the vibration case). For a
fast-decorrelating sample the reference decorrelates from late frames and the
shift estimate degrades there.
"""
import numpy as np
from scipy.ndimage import fourier_shift


def _parabolic(a, b, c):
    """Sub-pixel offset of a peak from its integer index, from 3 samples."""
    d = a - 2.0 * b + c
    return 0.0 if d == 0 else 0.5 * (a - c) / d


def realign_shift(ref_fft, img_fft):
    """Shift (sy, sx) that aligns img onto the reference, i.e.
    ``fourier_shift(img_fft, (sy, sx))`` brings img back onto ref. This is the
    phase-correlation peak; the frame's physical displacement is the negative."""
    cross = ref_fft * np.conj(img_fft)
    cross /= np.abs(cross) + 1e-12                   # whiten -> phase correlation
    corr = np.fft.ifft2(cross).real
    ny, nx = corr.shape
    py, px = np.unravel_index(np.argmax(corr), corr.shape)
    sy = py + _parabolic(corr[(py - 1) % ny, px], corr[py, px], corr[(py + 1) % ny, px])
    sx = px + _parabolic(corr[py, (px - 1) % nx], corr[py, px], corr[py, (px + 1) % nx])
    if sy > ny / 2:                                  # unwrap to a signed shift
        sy -= ny
    if sx > nx / 2:
        sx -= nx
    return sy, sx


def align_stack(frames, ref_index=0):
    """Register every frame to frames[ref_index] and realign it.
    Returns (aligned float32 stack, shifts[N, 2] = each frame's displacement vs
    the reference). One forward FFT per frame, reused for estimate + realign."""
    n, ny, nx = frames.shape
    ref_fft = np.fft.fft2(np.asarray(frames[ref_index], dtype=np.float64))
    aligned = np.empty((n, ny, nx), np.float32)
    shifts = np.zeros((n, 2))
    for k in range(n):
        F = np.fft.fft2(np.asarray(frames[k], dtype=np.float64))
        sy, sx = realign_shift(ref_fft, F)
        aligned[k] = np.fft.ifft2(fourier_shift(F, (sy, sx))).real
        shifts[k] = (-sy, -sx)                       # physical displacement vs ref
    return aligned, shifts


def shift_rms(shifts):
    """RMS displacement (px) of a shifts[N, 2] array, for reporting."""
    return float(np.sqrt((np.asarray(shifts) ** 2).sum(axis=1).mean()))
