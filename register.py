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


def _upsampled_dft(data, region, upsample, offsets):
    """Local upsampled inverse DFT of frequency-domain `data` over a
    region x region block at `upsample`x resolution, centred per `offsets`
    (Guizar-Sicairos matrix-multiply DFT -- no full upsampled FFT)."""
    out = data
    for n, off in list(zip(data.shape, offsets))[::-1]:
        kernel = np.exp(-2j * np.pi * (np.arange(region)[:, None] - off)
                        * np.fft.fftfreq(n, upsample))
        out = np.tensordot(kernel, out, axes=(1, -1))
    return out


def realign_shift(ref_fft, img_fft, upsample=10):
    """Shift (sy, sx) that aligns img onto the reference, i.e.
    ``fourier_shift(img_fft, (sy, sx))`` brings img back onto ref. Coarse peak
    of the whitened phase correlation, refined to ~1/upsample px by a local
    upsampled DFT (upsample<=1 falls back to a cheap parabolic peak)."""
    prod = ref_fft * np.conj(img_fft)
    prod /= np.abs(prod) + 1e-12                     # whiten -> phase correlation
    corr = np.fft.ifft2(prod).real
    ny, nx = corr.shape
    py, px = np.unravel_index(np.argmax(corr), corr.shape)

    if upsample <= 1:
        sy = py + _parabolic(corr[(py - 1) % ny, px], corr[py, px], corr[(py + 1) % ny, px])
        sx = px + _parabolic(corr[py, (px - 1) % nx], corr[py, px], corr[py, (px + 1) % nx])
        return (sy - ny if sy > ny / 2 else sy), (sx - nx if sx > nx / 2 else sx)

    sy = float(py - ny if py > ny // 2 else py)      # coarse, unwrapped
    sx = float(px - nx if px > nx // 2 else px)
    upsample = float(upsample)
    sy, sx = round(sy * upsample) / upsample, round(sx * upsample) / upsample
    region = int(np.ceil(upsample * 1.5))
    dftshift = region // 2
    offsets = (dftshift - sy * upsample, dftshift - sx * upsample)
    fine = np.abs(_upsampled_dft(np.conj(prod), region, upsample, offsets))
    my, mx = np.unravel_index(np.argmax(fine), fine.shape)
    return sy + (my - dftshift) / upsample, sx + (mx - dftshift) / upsample


def align_stack(frames, ref_index=0, upsample=10):
    """Register every frame to frames[ref_index] and realign it.
    Returns (aligned float32 stack, shifts[N, 2] = each frame's displacement vs
    the reference). One forward FFT per frame, reused for estimate + realign.
    `upsample` sets the sub-pixel resolution (~1/upsample px; <=1 = parabolic)."""
    n, ny, nx = frames.shape
    ref_fft = np.fft.fft2(np.asarray(frames[ref_index], dtype=np.float64))
    aligned = np.empty((n, ny, nx), np.float32)
    shifts = np.zeros((n, 2))
    for k in range(n):
        F = np.fft.fft2(np.asarray(frames[k], dtype=np.float64))
        sy, sx = realign_shift(ref_fft, F, upsample)
        aligned[k] = np.fft.ifft2(fourier_shift(F, (sy, sx))).real
        shifts[k] = (-sy, -sx)                       # physical displacement vs ref
    return aligned, shifts


def shift_rms(shifts):
    """RMS displacement (px) of a shifts[N, 2] array, for reporting."""
    return float(np.sqrt((np.asarray(shifts) ** 2).sum(axis=1).mean()))
