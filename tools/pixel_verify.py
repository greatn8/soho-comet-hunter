#!/usr/bin/env python3
"""Pixel-level evidence checks for realtime SOHO/LASCO C3 track hypotheses."""

from __future__ import annotations

import math
import statistics
import subprocess
from pathlib import Path


WIDTH = 512
HEIGHT = 512
PIXELS = WIDTH * HEIGHT


def _frame_time(path):
    name = Path(path).name
    from datetime import datetime, timezone
    return datetime.strptime(name[:13], "%Y%m%d_%H%M").replace(tzinfo=timezone.utc)


def _decode_gray(path, cache):
    key = str(path)
    if key in cache:
        return cache[key]

    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", str(path),
            "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0 or len(proc.stdout) != PIXELS:
        cache[key] = None
        return None

    cache[key] = proc.stdout
    return proc.stdout


def _median(values):
    return float(statistics.median(values)) if values else 0.0


def _sample_signal(gray, x, y):
    cx = int(round(x))
    cy = int(round(y))
    if cx < 11 or cy < 11 or cx >= WIDTH - 11 or cy >= HEIGHT - 11:
        return None

    core = []
    annulus = []
    near = []

    for dy in range(-10, 11):
        yy = cy + dy
        row = yy * WIDTH
        for dx in range(-10, 11):
            xx = cx + dx
            r2 = dx * dx + dy * dy
            value = gray[row + xx]
            if r2 <= 4:
                core.append(value)
            elif r2 <= 16:
                near.append(value)
            elif 36 <= r2 <= 100:
                annulus.append(value)

    if len(core) < 9 or len(annulus) < 80:
        return None

    background = _median(annulus)
    mad = _median([abs(v - background) for v in annulus])
    noise = max(1.5, 1.4826 * mad)

    # A sub-pixel source may not be centred perfectly on the fitted path.
    # Average the brightest compact pixels rather than requiring one exact
    # centre pixel, while still limiting the measurement to a 2-pixel radius.
    bright = sorted(core, reverse=True)[:5]
    core_signal = sum(bright) / len(bright)
    contrast = core_signal - background
    snr = contrast / noise

    near_signal = _median(near) - background if near else 0.0
    compactness = contrast / max(1.0, near_signal) if contrast > 0 else 0.0

    return {
        "snr": float(snr),
        "contrast": float(contrast),
        "background": float(background),
        "noise": float(noise),
        "compactness": float(compactness),
    }


def verify_track_pixels(candidate, frame_paths, gray_cache=None, max_samples=48):
    """Measure whether a compact source is repeatedly present on the fitted path.

    This intentionally verifies the underlying pixels rather than trusting the
    CUDA line fit. It is conservative: the result is evidence for ranking and
    review gating, while all raw track hypotheses remain available elsewhere.
    """
    cache = gray_cache if gray_cache is not None else {}
    first = candidate["first"]
    last = candidate["last"]
    first_t = first["time"]
    last_t = last["time"]

    eligible = []
    for path in frame_paths:
        try:
            when = _frame_time(path)
        except Exception:
            continue
        if first_t <= when <= last_t:
            eligible.append((Path(path), when))

    if len(eligible) > max_samples:
        # Uniform sampling preserves the full temporal span.
        indexes = sorted({
            int(round(i * (len(eligible) - 1) / (max_samples - 1)))
            for i in range(max_samples)
        })
        eligible = [eligible[i] for i in indexes]

    samples = []
    hit_flags = []
    longest_run = 0
    run = 0

    for path, when in eligible:
        gray = _decode_gray(path, cache)
        if gray is None:
            continue

        hours = (when - first_t).total_seconds() / 3600.0
        x = float(first["x"]) + float(candidate["vx"]) * hours
        y = float(first["y"]) + float(candidate["vy"]) * hours
        sample = _sample_signal(gray, x, y)
        if sample is None:
            continue

        sample["frame"] = path.name
        sample["x"] = x
        sample["y"] = y
        samples.append(sample)

        hit = sample["snr"] >= 2.5 and sample["contrast"] >= 2.0
        hit_flags.append(hit)
        if hit:
            run += 1
            longest_run = max(longest_run, run)
        else:
            run = 0

    count = len(samples)
    hits = sum(hit_flags)
    if not count:
        return {
            "status": "UNAVAILABLE",
            "samples": 0,
            "hits": 0,
            "hit_fraction": 0.0,
            "median_snr": 0.0,
            "peak_snr": 0.0,
            "median_contrast": 0.0,
            "median_compactness": 0.0,
            "longest_hit_run": 0,
            "evidence_score": 0.0,
        }

    snrs = [s["snr"] for s in samples]
    contrasts = [s["contrast"] for s in samples]
    compactness = [s["compactness"] for s in samples]
    hit_fraction = hits / count
    median_snr = _median(snrs)
    peak_snr = max(snrs)
    evidence_score = (
        55.0 * hit_fraction
        + 4.0 * min(max(median_snr, 0.0), 6.0)
        + 2.0 * min(longest_run, 8)
        + 1.5 * min(max(peak_snr, 0.0), 8.0)
    )

    # STRONG requires repeated compact evidence; SECONDARY deliberately keeps
    # marginal cases for human review. EMPTY means the fitted trajectory has
    # little pixel support and should not become a realtime comet alert.
    if count >= 5 and hit_fraction >= 0.40 and longest_run >= 3 and peak_snr >= 4.0:
        status = "STRONG"
    elif count >= 5 and hit_fraction >= 0.18 and longest_run >= 2 and peak_snr >= 3.0:
        status = "SECONDARY"
    elif count < 5:
        status = "UNAVAILABLE"
    else:
        status = "EMPTY"

    return {
        "status": status,
        "samples": count,
        "hits": hits,
        "hit_fraction": round(hit_fraction, 4),
        "median_snr": round(median_snr, 3),
        "peak_snr": round(peak_snr, 3),
        "median_contrast": round(_median(contrasts), 3),
        "median_compactness": round(_median(compactness), 3),
        "longest_hit_run": longest_run,
        "evidence_score": round(evidence_score, 2),
    }


def historical_recovery_profile(candidate):
    """Broad envelope containing the known historical recoveries.

    This is a safety fallback, not a definition of what a comet must be.
    """
    speed = float(candidate.get("speed", 0.0))
    rms = float(candidate.get("rms", 999.0))
    frames = int(candidate.get("frames", 0))
    sunward = float(candidate.get("sunward", 0.0))
    radial_fraction = sunward / speed if speed > 0 else -1.0
    return (
        frames >= 8
        and rms <= 0.85
        and 1.0 <= speed <= 9.5
        and radial_fraction >= 0.60
    )


def classify_for_review(candidate, pixel_result):
    pixel_status = str(pixel_result.get("status") or "UNAVAILABLE").upper()
    historical_like = historical_recovery_profile(candidate)

    if pixel_status == "STRONG":
        return "STRONG_REVIEW", historical_like
    if pixel_status == "SECONDARY":
        return "SECONDARY", historical_like
    if historical_like:
        # Preserve historically plausible candidates even when pixel evidence
        # is weak or unavailable so the verifier cannot erase known-like cases.
        return "SECONDARY", True
    return "VISUAL_REJECT", False
