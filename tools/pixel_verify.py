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


def configure_resolution(resolution):
    """Set native image dimensions for both decoding and pixel sampling."""
    global WIDTH, HEIGHT, PIXELS
    resolution = int(resolution)
    if resolution not in (512, 1024):
        raise ValueError("Unsupported LASCO image resolution")
    WIDTH = HEIGHT = resolution
    PIXELS = resolution * resolution


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

    # Keep decoded images bounded: 1024 frames occupy 1 MiB each.
    if len(cache) >= 96:
        cache.pop(next(iter(cache)))
    cache[key] = proc.stdout
    return proc.stdout


def _median(values):
    return float(statistics.median(values)) if values else 0.0


def _sample_signal(gray, x, y):
    cx = int(round(x))
    cy = int(round(y))
    scale = WIDTH // 512
    margin = 11 * scale
    if cx < margin or cy < margin or cx >= WIDTH - margin or cy >= HEIGHT - margin:
        return None

    core = []
    annulus = []
    near = []

    for dy in range(-10 * scale, 10 * scale + 1):
        yy = cy + dy
        row = yy * WIDTH
        for dx in range(-10 * scale, 10 * scale + 1):
            xx = cx + dx
            r2 = dx * dx + dy * dy
            value = gray[row + xx]
            if r2 <= 4 * scale * scale:
                core.append(value)
            elif r2 <= 16 * scale * scale:
                near.append(value)
            elif 36 * scale * scale <= r2 <= 100 * scale * scale:
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


def verify_track_pixels(candidate, frame_paths, gray_cache=None, max_samples=48, model="velocity"):
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
        if model == "endpoints":
            span = (last_t - first_t).total_seconds()
            if span <= 0:
                continue
            fraction = (when - first_t).total_seconds() / span
            x = float(first["x"]) + fraction * (float(last["x"]) - float(first["x"]))
            y = float(first["y"]) + fraction * (float(last["y"]) - float(first["y"]))
        elif model == "velocity":
            x = float(first["x"]) + float(candidate["vx"]) * hours
            y = float(first["y"]) + float(candidate["vy"]) * hours
        else:
            raise ValueError(f"Unknown trajectory model: {model}")
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
    speed = float(candidate.get("speed", 0.0)) / (WIDTH / 512.0)
    rms = float(candidate.get("rms", 999.0)) / (WIDTH / 512.0)
    frames = int(candidate.get("frames", 0))
    sunward = float(candidate.get("sunward", 0.0)) / (WIDTH / 512.0)
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


def verify_temporal_motion(candidate, frame_paths, gray_cache=None, model="velocity",
                           max_samples=18, min_separation_px=5.0):
    """Independent moving-source evidence against same-pixel background.

    At a tracked position in a middle frame, compare the compact source
    signal to the same position in earlier and later frames. A stationary
    feature or occulting-edge artifact should remain at that pixel; a moving
    compact source may appear only while passing through it.

    Diagnostic only until measured against historical known-comet controls.
    """
    cache = gray_cache if gray_cache is not None else {}
    min_separation_px *= WIDTH / 512.0
    first = candidate["first"]
    last = candidate["last"]
    t0, t1 = first["time"], last["time"]
    if t1 <= t0:
        return {"status": "UNAVAILABLE", "tested": 0, "supported": 0,
                "support_fraction": 0.0, "reason": "ZERO_TIME_SPAN"}
    eligible = []
    for f in frame_paths:
        try:
            t = _frame_time(f)
        except (TypeError, ValueError):
            continue
        if t0 <= t <= t1:
            eligible.append((Path(f), t))
    eligible.sort(key=lambda entry: entry[1])
    if len(eligible) < 5:
        return {"status": "UNAVAILABLE", "tested": 0, "supported": 0,
                "support_fraction": 0.0, "reason": "TOO_FEW_SOURCE_FRAMES"}

    def position(t):
        if model == "endpoints":
            fraction = (t - t0).total_seconds() / (t1 - t0).total_seconds()
            return (float(first["x"]) + fraction * (float(last["x"])-float(first["x"])),
                    float(first["y"]) + fraction * (float(last["y"])-float(first["y"])))
        hours = (t - t0).total_seconds() / 3600.0
        return (float(first["x"]) + hours * float(candidate["vx"]),
                float(first["y"]) + hours * float(candidate["vy"]))

    selected = list(range(1, len(eligible)-1))
    if len(selected) > max_samples:
        selected = sorted({selected[round(i*(len(selected)-1)/(max_samples-1))]
                           for i in range(max_samples)})
    tested = supported = 0
    excess_values = []
    for i in selected:
        target,t = eligible[i]
        x,y = position(t)
        # Seek controls distant enough in time that the fitted source should
        # have moved several pixels, not just to adjacent 12-minute images.
        left = next((j for j in range(i-1,-1,-1)
                     if math.dist(position(eligible[j][1]),(x,y)) >= min_separation_px),None)
        right = next((j for j in range(i+1,len(eligible))
                      if math.dist(position(eligible[j][1]),(x,y)) >= min_separation_px),None)
        if left is None or right is None:
            continue
        current = _decode_gray(target,cache)
        before = _decode_gray(eligible[left][0],cache)
        after = _decode_gray(eligible[right][0],cache)
        if current is None or before is None or after is None:
            continue
        a = _sample_signal(current,x,y)
        b = _sample_signal(before,x,y)
        c = _sample_signal(after,x,y)
        if a is None or b is None or c is None:
            continue
        baseline = max(b["contrast"],c["contrast"])
        excess = a["contrast"] - baseline
        tested += 1
        excess_values.append(excess)
        # Requiring significance against the same position is intentionally
        # more resistant to persistent background features than spatial SNR.
        if a["snr"] >= 2.5 and excess >= max(3.0,2.0*a["noise"]):
            supported += 1
    frac = supported/tested if tested else 0.0
    return {"status":"MEASURED" if tested>=3 else "UNAVAILABLE",
            "tested":tested,"supported":supported,
            "support_fraction":round(frac,4),
            "median_excess":round(_median(excess_values),3)}
