#!/usr/bin/env python3
"""Independent, offline CCOR FITS-versus-JPEG geometry survey.

Matches archived FITS and NOAA Level-3 JPEGs by observation timestamps without
using any reported comet positions. Emits full-frame side-by-side views and
WCS metadata for review, NOT an established FITS-to-JPEG registration.

Never reads/writes production comet results, changes live caches, downloads
frames, modifies alerts, or claims a comet recovery.

Dependencies: numpy, pillow, astropy.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import ccor_hunter as hunter
from ccor_blind_jpeg_replay import CASES, validate_jpeg
from ccor_native_fits_audit import load_fits

UTC = dt.timezone.utc
WCS_KEYS = (
    "CTYPE1", "CTYPE2", "CRPIX1", "CRPIX2", "CRVAL1", "CRVAL2",
    "CDELT1", "CDELT2", "PC1_1", "PC1_2", "PC2_1", "PC2_2",
    "PV1_0", "PV1_1", "PV1_2", "PV2_0", "PV2_1", "PV2_2",
)
MAX_FITS = 24
MAX_MATCH_DELTA_SECONDS = 120


def timestamp(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def orientation_degrees(wcs):
    """Linear PC orientation; NOT the rotation from FITS to NOAA JPEG."""
    try:
        return round(math.degrees(math.atan2(
            float(wcs["PC2_1"]), float(wcs["PC1_1"]))), 5)
    except (ValueError, TypeError, KeyError, ZeroDivisionError):
        return None


def closest_index(t, entries, max_seconds=MAX_MATCH_DELTA_SECONDS):
    if not entries:
        return None
    delta, index = min(
        (abs((entry["time"] - t).total_seconds()), i)
        for i, entry in enumerate(entries)
    )
    return index if delta <= max_seconds else None


def image_stretch(array):
    """Presentation-only FITS intensity stretch; scientific data unchanged."""
    a = np.asarray(array)
    valid = a[np.isfinite(a)]
    if not valid.size:
        return Image.new("RGB", (a.shape[1], a.shape[0]), (20, 20, 20)), None
    p_low, p_high = np.percentile(valid, [1.0, 99.7])
    if p_high <= p_low:
        p_high = p_low + 1
    normalized = np.nan_to_num(
        (a.astype(np.float32) - p_low) / (p_high - p_low),
        nan=0.0, posinf=1.0, neginf=0.0
    )
    gray = np.uint8(np.clip(normalized, 0, 1) * 255)
    return Image.fromarray(gray, "L").convert("RGB"), {
        "display_percentile_low": round(float(p_low), 5),
        "display_percentile_high": round(float(p_high), 5),
    }


def grid_preview(source, box=(520, 485)):
    """Uniform normalized grid, not a proposed source-to-JPEG transform."""
    img = source.copy()
    img.thumbnail(box, Image.Resampling.BILINEAR)
    draw = ImageDraw.Draw(img)
    for n in range(1, 8):
        x = round(n * (img.width - 1) / 8)
        y = round(n * (img.height - 1) / 8)
        draw.line([(x, 0), (x, img.height - 1)], fill=(245, 181, 25), width=1)
        draw.line([(0, y), (img.width - 1, y)], fill=(245, 181, 25), width=1)
    return img


def pair_contact_sheet(fits_array, jpeg, fits_name, jpeg_name, output):
    fits_img, stretch = image_stretch(fits_array)
    left = grid_preview(fits_img)
    right = grid_preview(jpeg.convert("RGB"))
    canvas = Image.new("RGB", (1100, 565), (16, 22, 34))
    draw = ImageDraw.Draw(canvas)
    draw.text((10, 8), "CCOR FITS / NOAA JPEG: time-matched but NOT registered",
              fill=(255, 215, 84))
    draw.text((12, 38), "FITS array rows displayed top-down (not sky orientation)",
              fill="white")
    draw.text((562, 38), "NOAA JPEG as delivered (includes presentation graphics)",
              fill="white")
    canvas.paste(left, (10, 64))
    canvas.paste(right, (562, 64))
    draw.text((10, 542), fits_name[:75], fill=(200, 220, 235))
    draw.text((562, 542), jpeg_name[:75], fill=(200, 220, 235))
    canvas.save(output)
    return stretch


def source_manifest(case, jpeg_root):
    base = jpeg_root / case / "jpegs"
    if not base.is_dir():
        raise FileNotFoundError("Archived blind JPEG folder missing: " + str(base))
    images = []
    camera = CASES[case]["camera"]
    for path in sorted(base.glob("*.jpg")):
        if (hunter.STAMP_RE.fullmatch(path.name) is None
                or ("_" + camera + "_") not in path.name.lower()):
            continue
        images.append({
            "path": path,
            "name": path.name,
            "time": hunter.stamp(path.name),
        })
    return images


def discover_fits(fits_root, camera):
    paths = sorted((fits_root / camera).glob("*.fits"))
    if len(paths) > MAX_FITS:
        raise ValueError("Exceeded safety limit of 24 source FITS per camera")
    return paths


def run_case(case, fits_root, jpeg_root, output_root, make_images=True):
    camera = CASES[case]["camera"]
    fits_paths = discover_fits(fits_root, camera)
    jpegs = source_manifest(case, jpeg_root)
    for entry in jpegs:
        entry["meta"] = validate_jpeg(entry["path"])
        with Image.open(entry["path"]) as img:
            entry["dimensions_xy"] = list(img.size)
    frames = []
    rows = []
    for path in fits_paths:
        frame = load_fits(path)
        if frame["camera"] != camera:
            raise ValueError("Unexpected camera in FITS: " + str(path))
        frames.append(frame)
    frames.sort(key=lambda f: f["timestamp"])
    output_root.mkdir(parents=True, exist_ok=True)
    chosen = set((0, len(frames) // 2, len(frames) - 1))
    for index, frame in enumerate(frames):
        j = closest_index(frame["timestamp"], jpegs)
        matched = jpegs[j] if j is not None else None
        row = {
            "fits_file": Path(frame["path"]).name,
            "fits_timestamp_utc": hunter.iso(frame["timestamp"]),
            "fits_time_source": frame["time_source"],
            "fits_shape_yx": frame["shape"],
            "fits_product_level": frame["processing_level"],
            "fits_wcs_metadata": frame["wcs"],
            "fits_pc_orientation_deg": orientation_degrees(frame["wcs"]),
            "matched_jpeg": matched["name"] if matched else None,
            "matched_jpeg_delta_seconds": (
                round((frame["timestamp"] - matched["time"]).total_seconds(), 2)
                if matched else None
            ),
            "matched_jpeg_shape_xy": matched["dimensions_xy"] if matched else None,
            "matched_jpeg_sha256": matched["meta"]["sha256"] if matched else None,
            "fullframe_sheet": None,
        }
        if make_images and matched and index in chosen:
            target = output_root / f"{case}_pair_{index:02}.png"
            with Image.open(matched["path"]) as jpeg:
                row["fits_presentation_stretch"] = pair_contact_sheet(
                    frame["array"], jpeg, row["fits_file"],
                    matched["name"], target)
            row["fullframe_sheet"] = str(target)
        rows.append(row)
        print(f"{camera} {index+1}/{len(frames)} FITS={row['fits_file']} "
              f"JPEG={row['matched_jpeg']} "
              f"delta_s={row['matched_jpeg_delta_seconds']} "
              f"PCdeg={row['fits_pc_orientation_deg']}", flush=True)
    angles = [r["fits_pc_orientation_deg"] for r in rows
              if r["fits_pc_orientation_deg"] is not None]
    report = {
        "case": case,
        "camera": camera,
        "mode": "OFFLINE_INDEPENDENT_CCOR_FITS_JPEG_LAYOUT_SURVEY",
        "registration_established": False,
        "comet_report_positions_loaded": False,
        "comet_recovery_claim": None,
        "network_access": False,
        "production_modified": False,
        "fits_count": len(frames),
        "jpeg_count": len(jpegs),
        "matched_frame_count": sum(r["matched_jpeg"] is not None for r in rows),
        "fits_pc_orientation_range_degrees": (
            [min(angles), max(angles)] if angles else None
        ),
        "frames": rows,
        "warning": (
            "PC matrix describes FITS-to-solar coordinates, not a FITS-to-JPEG "
            "transformation. Level-1 and Level-3 products differ in processing. "
            "Uniform preview grids and brightness stretches are not calibrations."
        )
    }
    dest = output_root / (case + "_geometry_manifest.json")
    if dest.exists():
        raise FileExistsError("Refusing to overwrite prior report: " + str(dest))
    dest.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{case}: {report['matched_frame_count']}/{len(frames)} "
          f"time-matched FITS/JPEG; saved {dest}", flush=True)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fits-dir", type=Path, required=True)
    p.add_argument("--jpeg-dir", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--case", choices=tuple(CASES), action="append")
    p.add_argument("--no-images", action="store_true")
    a = p.parse_args()
    fits_root = a.fits_dir.expanduser().resolve()
    jpeg_root = a.jpeg_dir.expanduser().resolve()
    output_root = a.output_dir.expanduser().resolve()
    if (not fits_root.is_dir() or not jpeg_root.is_dir()
            or output_root in (fits_root, jpeg_root)
            or output_root in fits_root.parents
            or output_root in jpeg_root.parents
            or fits_root in output_root.parents
            or jpeg_root in output_root.parents
            or "comet_hunter_native_cuda_v7_archive" in output_root.parts):
        p.error("Input directories must exist and output must be separate")
    failed = False
    for case in dict.fromkeys(a.case or list(CASES)):
        try:
            run_case(case, fits_root, jpeg_root, output_root,
                     make_images=not a.no_images)
        except Exception as exc:
            failed = True
            print(f"{case}: FAILED: {type(exc).__name__}: {exc}", flush=True)
    if failed:
        raise SystemExit(2)
    print("Offline geometry survey completed; no registration or comet claim.")


if __name__ == "__main__":
    main()
