#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

VIDEO_PREFERENCE = (
    "zoom_annotated.mp4",
    "full_annotated.mp4",
    "zoom_raw.mp4",
    "full_raw.mp4",
    "zoom_difference.mp4",
)
IMAGE_PATTERNS = ("*.png", "*.jpg", "*.jpeg", "*.webp")
MAX_MEDIA_MB = 50
LIVE_MEDIA_STAGE = Path(os.environ.get("COMET_LIVE_MEDIA_STAGE", "/dev/shm/soho_comet_dashboard_live_media"))
LIVE_MEDIA_BASE = os.environ.get(
    "COMET_LIVE_MEDIA_BASE",
    "https://raw.githubusercontent.com/greatn8/soho-comet-hunter/live-media",
).rstrip("/")
LIVE_MEDIA_LIMIT = 100

FIELD_ALIASES = {
    "score": ("score", "review_score", "rank_score", "ranking_score", "event_score", "total_score"),
    "speed": ("speed", "event_speed", "track_speed", "speed_px_frame", "speed_px_per_frame", "velocity"),
    "sun_distance": ("sun", "avg_sunward", "sunward", "sun_distance", "solar_distance", "sun_dist", "rho"),
    "rms": ("rms", "best_rms", "fit_rms", "track_rms", "residual_rms"),
    "frames": ("frames", "max_frames", "nframes", "frame_count", "num_frames"),
    "members": ("members", "member_count", "n_members", "num_members"),
    "family": ("family", "comet_family"),
    "first_seen": ("first_seen", "start_time", "timestamp", "time", "date"),
    "review_class": ("review_class", "class", "category", "tier", "status", "label"),
}

def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def iso_from_mtime(path):
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")

def clean(v):
    if v is None:
        return None
    s = str(v).strip()
    return None if not s or s.lower() in {"nan", "none", "null", "-"} else s

def number(v):
    v = clean(v)
    if v is None:
        return None
    try:
        n = float(v)
        return int(n) if n.is_integer() else n
    except ValueError:
        return v

def norm_id(value):
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())

def candidate_keys(candidate_id):
    full = norm_id(candidate_id)
    base = norm_id(re.sub(r"_\d+$", "", candidate_id))
    return {x for x in (full, base) if x}

def infer_source(path):
    text = str(path).lower()
    if "c2" in text:
        return "SOHO C2", "LASCO C2"
    if "c3" in text:
        return "SOHO C3", "LASCO C3"
    return "SOHO", "LASCO"

def infer_family(path):
    text = str(path).lower()
    for family in ("kreutz", "meyer", "marsden", "kracht"):
        if family in text:
            return family.capitalize()
    return "Unknown"

def load_json_metadata(folder):
    for name in ("dashboard.json", "candidate.json"):
        p = folder / name
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"[WARN] Could not parse {p}: {exc}")
    return {}

def first_matching(folder, patterns):
    for pattern in patterns:
        matches = sorted(folder.glob(pattern))
        if matches:
            return matches[0]
    return None

def preferred_video(folder):
    for name in VIDEO_PREFERENCE:
        p = folder / name
        if p.exists():
            return p
    videos = sorted(folder.glob("*.mp4"))
    return videos[0] if videos else None

def preferred_thumbnail(folder):
    contact = folder / "zoom_contact_sheet.jpg"
    if contact.exists():
        return contact
    return first_matching(folder, IMAGE_PATTERNS)

def verified_frame_count(folder):
    manifest = folder / "manifest.tsv"
    if not manifest.exists():
        return None
    try:
        lines = [line for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
        if not lines:
            return None
        # manifest.tsv normally has one header row followed by one row per downloaded frame.
        return max(0, len(lines) - 1)
    except Exception:
        return None

def count_frames(folder):
    exts = {".png", ".jpg", ".jpeg", ".webp", ".fits", ".fit"}
    return sum(1 for p in folder.iterdir() if p.is_file() and p.suffix.lower() in exts)

def safe_copy(source, destination):
    size_mb = source.stat().st_size / (1024 * 1024)
    if size_mb > MAX_MEDIA_MB:
        print(f"[WARN] Skipping {source.name}: {size_mb:.1f} MB exceeds {MAX_MEDIA_MB} MB.")
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination.name

def discover_candidate_folders(source):
    folders = {video.parent.resolve() for video in source.rglob("*.mp4")}
    found = []
    for folder in folders:
        video = preferred_video(folder)
        if video is not None:
            found.append((folder, video))
    return sorted(found, key=lambda x: x[1].stat().st_mtime, reverse=True)

def metadata_files(results_root):
    out = []
    for ext in ("*.tsv", "*.txt", "*.log"):
        for p in results_root.rglob(ext):
            name = p.name.lower()
            if any(word in name for word in ("candidate", "event", "strong", "secondary", "rank", "summary")):
                out.append(p)
    return sorted(set(out))

def row_lookup(row, aliases):
    low = {str(k).strip().lower(): clean(v) for k, v in row.items()}
    for key in aliases:
        if low.get(key) is not None:
            return low[key]
    return None

def parse_key_values(line):
    result = {}
    for key, value in re.findall(r"([A-Za-z_]+)\s*=\s*([^\s\t,]+)", line):
        result[key.lower()] = value
    if "STRONG_REVIEW" in line.upper():
        result["review_class"] = "STRONG_REVIEW"
    elif "SECONDARY" in line.upper():
        result["review_class"] = "SECONDARY"
    return result

def load_metadata_records(results_root):
    records = []
    for p in metadata_files(results_root):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            print(f"[WARN] Could not read {p}: {exc}")
            continue

        # Headered TSV rows.
        lines = text.splitlines()
        if lines and "\t" in lines[0]:
            try:
                reader = csv.DictReader(lines, delimiter="\t")
                for row in reader:
                    records.append({
                        "file": p.name,
                        "raw": "\t".join("" if v is None else str(v) for v in row.values()),
                        "row": row,
                    })
            except Exception:
                pass

        # Also retain every raw line so headerless/key=value summaries work.
        for line in lines:
            if line.strip():
                records.append({"file": p.name, "raw": line, "row": parse_key_values(line)})

        print(f"[INFO] Scanned metadata: {p}")
    return records

def match_record(record, candidate_id):
    raw_norm = norm_id(record.get("raw", ""))
    keys = candidate_keys(candidate_id)
    return any(k and k in raw_norm for k in keys)

def extract_record_fields(record):
    row = record.get("row", {})
    out = {}
    for field, aliases in FIELD_ALIASES.items():
        value = row_lookup(row, aliases)
        if value is not None:
            out[field] = value

    raw = record.get("raw", "")
    if "review_class" not in out:
        upper = raw.upper()
        if "STRONG_REVIEW" in upper:
            out["review_class"] = "STRONG_REVIEW"
        elif "SECONDARY" in upper:
            out["review_class"] = "SECONDARY"

    # Fall back to key=value extraction from any line.
    kv = parse_key_values(raw)
    for field, aliases in FIELD_ALIASES.items():
        if field in out:
            continue
        for alias in aliases:
            if alias in kv:
                out[field] = kv[alias]
                break

    for field in ("score", "speed", "sun_distance", "rms", "frames", "members"):
        if field in out:
            out[field] = number(out[field])
    return out

def pipeline_metadata(candidate_id, records):
    matches = [r for r in records if match_record(r, candidate_id)]
    merged = {}

    # Prefer candidate summaries, then strong/secondary tables, then general event/rank files.
    def priority(r):
        name = r["file"].lower()
        if "candidate_summary" in name: return 0
        if "strong" in name: return 1
        if "secondary" in name: return 2
        if "event" in name or "rank" in name: return 3
        return 4

    for record in sorted(matches, key=priority):
        fields = extract_record_fields(record)
        for k, v in fields.items():
            if merged.get(k) is None:
                merged[k] = v
    return merged


def load_merged_candidates(results_root):
    path = results_root / "merged_candidates.tsv"
    out = {}
    if not path.exists():
        return out

    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.reader(f, delimiter="\t")
        rows = list(reader)

    if not rows:
        return out

    # Detect whether the first row is a header.
    header = [str(x).strip().lower() for x in rows[0]]
    has_header = any(x in header for x in ("group_id", "candidate_id", "id", "score", "frames"))
    data_rows = rows[1:] if has_header else rows

    # The current merged_candidates.tsv layout is:
    # GID, run_id, chunk_start, chunk_end, priority, candidate_id, members,
    # score, frames, speed, vx, vy, rms, brightness_cv, sunward,
    # first_fname, first_x, first_y, last_fname, last_x, last_y, member_ids
    for row in data_rows:
        if not row:
            continue
        gid = clean(row[0]) if len(row) > 0 else None
        if not gid or not gid.upper().startswith("G"):
            continue

        try:
            out[gid.upper()] = {
                "chunk_start": clean(row[2]) if len(row) > 2 else None,
                "priority": clean(row[4]) if len(row) > 4 else None,
                "candidate_id": clean(row[5]) if len(row) > 5 else None,
                "members": number(row[6]) if len(row) > 6 else None,
                "score": number(row[7]) if len(row) > 7 else None,
                "frames": number(row[8]) if len(row) > 8 else None,
                "speed": number(row[9]) if len(row) > 9 else None,
                "rms": number(row[12]) if len(row) > 12 else None,
                "sun_distance": number(row[14]) if len(row) > 14 else None,
                "first_fname": clean(row[15]) if len(row) > 15 else None,
                "last_fname": clean(row[18]) if len(row) > 18 else None,
                "member_ids": clean(row[21]) if len(row) > 21 else None,
            }
        except Exception:
            continue

    print(f"[INFO] Loaded merged candidate metrics for {len(out)} groups.")
    return out


def load_event_classes(results_root):
    path = results_root / "v9_events_all.tsv"
    by_member = {}
    if not path.exists():
        return by_member

    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                lane = clean(row.get("lane"))
                event_id = clean(row.get("event_id"))
                members = clean(row.get("member_ids"))
                review_score = number(row.get("review_score"))
                event_speed = number(row.get("event_speed"))
                best_rms = number(row.get("best_rms"))
                sunward = number(row.get("avg_sunward"))
                max_frames = number(row.get("max_frames"))
                member_count = number(row.get("members"))

                if not members:
                    continue

                for token in members.split(","):
                    token = token.strip().upper()
                    if not token:
                        continue
                    # Keep the full dated key, e.g. 2026-08-31:C02.
                    # Cxx IDs repeat across archive chunks, so suffix-only
                    # matching can silently select the wrong event.
                    by_member[token] = {
                        "review_class": lane,
                        "event_id": event_id,
                        "event_score": review_score,
                        "event_speed": event_speed,
                        "event_rms": best_rms,
                        "event_sunward": sunward,
                        "event_frames": max_frames,
                        "event_members": member_count,
                    }
    except Exception as exc:
        print(f"[WARN] Could not parse {path}: {exc}")

    print(f"[INFO] Loaded event classifications for {len(by_member)} dated member keys.")
    return by_member


def load_events_by_id(results_root):
    path = results_root / "v9_events_all.tsv"
    out = {}
    if not path.exists():
        return out

    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                event_id = clean(row.get("event_id"))
                if not event_id:
                    continue
                camera = (clean(row.get("camera")) or "").lower()
                source = f"SOHO {camera.upper()}" if camera in {"c2", "c3"} else "SOHO"
                instrument = f"LASCO {camera.upper()}" if camera in {"c2", "c3"} else "LASCO"
                out[event_id.upper()] = {
                    "event_id": event_id,
                    "review_class": clean(row.get("lane")),
                    "score": number(row.get("review_score")),
                    "speed": number(row.get("event_speed")),
                    "sun_distance": number(row.get("avg_sunward")),
                    "rms": number(row.get("best_rms")),
                    "frames": number(row.get("max_frames")),
                    "members": number(row.get("members")),
                    "first_seen": clean(row.get("start_iso")) or clean(row.get("start_time")) or clean(row.get("start")),
                    "last_seen": clean(row.get("end_iso")) or clean(row.get("end_time")) or clean(row.get("end")),
                    "source": source,
                    "instrument": instrument,
                }
    except Exception as exc:
        print(f"[WARN] Could not parse stable events from {path}: {exc}")

    print(f"[INFO] Loaded stable event metadata for {len(out)} events.")
    return out

def merged_group_id(candidate_id):
    return re.sub(r"_\d+$", "", str(candidate_id)).upper()


def event_for_merged(meta, event_classes):
    chunk_start = clean(meta.get("chunk_start"))
    ids = []

    member_ids = clean(meta.get("member_ids"))
    candidate_id = clean(meta.get("candidate_id"))

    if member_ids:
        ids.extend(x.strip().upper() for x in member_ids.split(",") if x.strip())
    if candidate_id:
        ids.append(candidate_id.upper())

    # Exact dated match prevents collisions such as C02 appearing in
    # multiple archive chunks.
    if chunk_start:
        for mid in ids:
            dated_key = f"{chunk_start}:{mid}".upper()
            if dated_key in event_classes:
                return event_classes[dated_key]

    return {}

def build_candidate(folder, video, media_dir, copy_media, records, merged_candidates, event_classes, events_by_id):
    json_meta = load_json_metadata(folder)
    candidate_id = str(json_meta.get("id") or folder.name)

    meta = pipeline_metadata(candidate_id, records)

    # Stable verification folders use IDs such as EV20260610T0918_36764F_1024.
    # Prefer the exact v9 event row for these folders.
    stable_id = re.sub(r"_\d+$", "", candidate_id).upper()
    stable_meta = events_by_id.get(stable_id, {})
    for key, value in stable_meta.items():
        if value is not None:
            meta[key] = value

    gid = merged_group_id(candidate_id)
    merged_meta = merged_candidates.get(gid, {})
    for key, value in merged_meta.items():
        if value is not None:
            meta[key] = value

    event_meta = event_for_merged(merged_meta, event_classes)
    if event_meta.get("review_class"):
        meta["review_class"] = event_meta["review_class"]
    if event_meta.get("event_id"):
        meta["event_id"] = event_meta["event_id"]

    # Keep group-level score/speed/RMS from merged_candidates because they
    # describe the exact Gxxxx visual candidate. Event-level values remain
    # available through the classification but do not overwrite them.
    meta.update({k: v for k, v in json_meta.items() if v is not None})

    source_name, instrument = infer_source(folder)
    video_url = None
    thumb_url = None

    if copy_media:
        clean_id = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in candidate_id)
        out_dir = media_dir / clean_id
        copied_video = safe_copy(video, out_dir / video.name)
        if copied_video:
            video_url = f"./media/{clean_id}/{copied_video}"

        thumb = preferred_thumbnail(folder)
        if thumb:
            thumb_name = safe_copy(thumb, out_dir / f"preview{thumb.suffix.lower()}")
            if thumb_name:
                thumb_url = f"./media/{clean_id}/{thumb_name}"

    frames = meta.get("frames")
    if frames is None:
        n = count_frames(folder)
        frames = n if n else None

    verified_frames = verified_frame_count(folder)

    return {
        "id": candidate_id,
        "title": meta.get("title") or f"Candidate {candidate_id}",
        "score": meta.get("score"),
        "review_class": meta.get("review_class") or "UNCLASSIFIED",
        "event_id": meta.get("event_id"),
        "speed": meta.get("speed"),
        "sun_distance": meta.get("sun_distance"),
        "rms": meta.get("rms"),
        "members": meta.get("members"),
        "family": meta.get("family") or infer_family(folder),
        "source": meta.get("source") or source_name,
        "instrument": meta.get("instrument") or instrument,
        "first_seen": meta.get("first_seen") or iso_from_mtime(video),
        "last_seen": meta.get("last_seen"),
        "frames": frames,
        "verified_frames": verified_frames,
        "verified_at": iso_from_mtime(video),
        "video_variant": video.stem,
        "notes": meta.get("notes") or "Automatically added from the comet-hunter results folder. Human review required.",
        "video": meta.get("video") or video_url,
        "thumbnail": meta.get("thumbnail") or thumb_url,
    }


def file_iso(path):
    try:
        return iso_from_mtime(path)
    except Exception:
        return None


def read_tsv_records(path):
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            return list(csv.DictReader(f, delimiter="\t"))
    except Exception as exc:
        print(f"[WARN] Could not parse realtime TSV {path}: {exc}")
        return []



FRAME_NAME_RE = re.compile(
    r"(?P<date>20\\d{6})_(?P<hm>\\d{4})_c[23]_(?:512|1024)\\.jpg",
    re.IGNORECASE,
)


def frame_iso(frame_name):
    if not frame_name:
        return None
    match = FRAME_NAME_RE.search(str(frame_name))
    if not match:
        return None
    try:
        value = datetime.strptime(
            match.group("date") + match.group("hm"),
            "%Y%m%d%H%M",
        ).replace(tzinfo=timezone.utc)
        return value.isoformat().replace("+00:00", "Z")
    except ValueError:
        return None


def realtime_archive_id(row):
    last = clean(row.get("last")) or "unknown"
    candidate = clean(row.get("candidate")) or "CXX"
    match = FRAME_NAME_RE.search(last)
    stamp = (match.group("date") + "T" + match.group("hm")) if match else norm_id(last)[:20]
    return f"RT{stamp}_{norm_id(candidate)}"



def find_realtime_review_id(source, row):
    explicit = clean(row.get("review_id"))
    if explicit:
        return explicit
    first = clean(row.get("first")); last = clean(row.get("last"))
    root = source / "review"
    if not root.exists():
        return None
    for meta_path in root.glob("*/review.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if first and last and meta.get("first") == first and meta.get("last") == last:
            return clean(meta.get("review_id")) or meta_path.parent.name
    return None

def publish_realtime_review_assets(source, data_dir, row):
    # Publish exactly one stitched annotated zoom video plus its lightweight
    # poster image for live human review. These files are staged in /dev/shm
    # and pushed to a force-replaced live-media branch, so old clips do not
    # accumulate in the main dashboard Git history.
    result = dict(row)
    review_id = find_realtime_review_id(source, row)
    for field in ("review_video","review_raw_video","review_full_video","review_thumbnail"):
        result.pop(field,None)
    if not review_id:
        return result

    result["review_id"] = review_id
    folder = source / "review" / str(review_id)
    video = folder / "zoom_annotated.mp4"
    thumb = folder / "zoom_contact_sheet.jpg"
    target_dir = LIVE_MEDIA_STAGE / str(review_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    if video.exists():
        shutil.copy2(video, target_dir / "zoom_annotated.mp4")
        result["review_video"] = f"{LIVE_MEDIA_BASE}/{review_id}/zoom_annotated.mp4"

    if thumb.exists():
        shutil.copy2(thumb, target_dir / "zoom_contact_sheet.jpg")
        result["review_thumbnail"] = f"{LIVE_MEDIA_BASE}/{review_id}/zoom_contact_sheet.jpg"

    return result

def build_realtime_archive_candidate(row, status, promoted_at):
    first_seen = frame_iso(row.get("first"))
    last_seen = frame_iso(row.get("last"))
    candidate_id = realtime_archive_id(row)
    report_id = clean(row.get("report_id"))

    if status == "KNOWN_REPORT":
        review_class = "KNOWN_REPORT"
        notes = (
            "Promoted automatically from the realtime hunter after 24 hours. "
            "The live trajectory matched a recent Sungrazer report. "
            "Human review is still recommended before treating the identity as definitive."
        )
    else:
        review_class = "REALTIME_UNRESOLVED"
        notes = (
            "Promoted automatically from the realtime hunter after 24 hours. "
            "No recent Sungrazer trajectory match was found when this alert was created."
        )

    return {
        "id": candidate_id,
        "title": f"Realtime candidate {candidate_id}",
        "score": number(row.get("score")),
        "review_class": review_class,
        "event_id": candidate_id,
        "speed": number(row.get("speed")),
        "sun_distance": number(row.get("sunward")),
        "rms": number(row.get("rms")),
        "members": 1,
        "family": "Unknown",
        "source": "SOHO C3 realtime",
        "instrument": "LASCO C3",
        "first_seen": first_seen,
        "last_seen": last_seen,
        "frames": number(row.get("frames")),
        "verified_frames": None,
        "verified_at": promoted_at,
        "video_variant": "realtime track",
        "notes": notes,
        "video": None,
        "thumbnail": None,
        "archived_from_realtime": True,
        "realtime_status": status,
        "known_object": report_id,
        "promoted_at": promoted_at,
    }


def promote_realtime_candidates(realtime_source, data_dir, age_hours=24):
    archive_path = data_dir / "realtime_archive.json"
    archive = {"candidates": []}

    if archive_path.exists():
        try:
            loaded = json.loads(archive_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("candidates"), list):
                archive = loaded
        except Exception as exc:
            print(f"[WARN] Could not parse {archive_path}: {exc}")

    by_id = {
        str(c.get("id")): c
        for c in archive.get("candidates", [])
        if isinstance(c, dict) and c.get("id")
    }

    if realtime_source is None:
        return list(by_id.values())

    source = Path(realtime_source).expanduser().resolve()
    if not source.exists():
        return list(by_id.values())

    rows = []
    for row in read_tsv_records(source / "alerts.tsv"):
        rows.append((row, "UNMATCHED"))
    for row in read_tsv_records(source / "known_matches.tsv"):
        rows.append((row, "KNOWN_REPORT"))

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=age_hours)
    promoted = 0

    for row, status in rows:
        last_seen_text = frame_iso(row.get("last"))
        if not last_seen_text:
            continue
        try:
            last_seen = datetime.fromisoformat(last_seen_text.replace("Z", "+00:00"))
        except ValueError:
            continue
        if last_seen > cutoff:
            continue

        promoted_at = now.isoformat().replace("+00:00", "Z")
        review_id = find_realtime_review_id(source, row)
        candidate = build_realtime_archive_candidate(row, status, promoted_at)
        candidate["review_id"] = review_id
        # Realtime live-media clips are intentionally ephemeral and are not
        # attached to the permanent candidate archive.
        candidate["video"] = None
        candidate["thumbnail"] = None
        candidate["review_raw_video"] = None
        candidate["review_full_video"] = None
        existing = by_id.get(candidate["id"])

        if existing:
            # A later known-report match should enrich an earlier unresolved archive row.
            if status == "KNOWN_REPORT":
                existing["review_class"] = candidate["review_class"]
                existing["realtime_status"] = status
                existing["known_object"] = candidate.get("known_object")
                existing["notes"] = candidate["notes"]
            for field in ("score", "speed", "sun_distance", "rms", "frames"):
                if existing.get(field) is None and candidate.get(field) is not None:
                    existing[field] = candidate[field]
            continue

        by_id[candidate["id"]] = candidate
        promoted += 1

    candidates = sorted(
        by_id.values(),
        key=lambda c: str(c.get("last_seen") or ""),
        reverse=True,
    )
    new_archive = {
        "generated_at": now_iso(),
        "promotion_age_hours": age_hours,
        "candidates": candidates,
    }

    previous = None
    if archive_path.exists():
        try:
            previous = json.loads(archive_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    if previous != new_archive:
        archive_path.write_text(json.dumps(new_archive, indent=2), encoding="utf-8")

    if promoted:
        print(f"[OK] Promoted {promoted} realtime candidate(s) into the permanent archive.")
    else:
        print("[OK] No realtime candidates reached the archive age threshold this cycle.")

    return candidates


def update_realtime_html_fallback(data_dir):
    realtime_path = data_dir / "realtime.json"
    index_path = data_dir.parent / "index.html"
    if not realtime_path.exists() or not index_path.exists():
        return

    try:
        payload = json.loads(realtime_path.read_text(encoding="utf-8"))
    except Exception:
        return

    frame = clean(payload.get("last_frame"))
    if frame:
        frame = re.sub(r"_c3_512\\.jpg$", "", frame, flags=re.IGNORECASE)
    else:
        frame = "—"

    text = index_path.read_text(encoding="utf-8")
    updated = re.sub(
        r'(<strong id="realtimeLastFrame"[^>]*>).*?(</strong>)',
        lambda m: m.group(1) + frame + m.group(2),
        text,
        count=1,
        flags=re.DOTALL,
    )
    if updated != text:
        index_path.write_text(updated, encoding="utf-8")
        print(f"[OK] Embedded latest realtime frame in dashboard HTML: {frame}")


def publish_realtime_data(realtime_source, data_dir):
    output = data_dir / "realtime.json"

    if realtime_source is None:
        payload = {
            "available": False,
            "source_updated_at": None,
            "last_frame": None,
            "latest_frame_image": None,
            "hunter_heartbeat_at": None,
            "hunter_status": None,
            "hunter_new_frames": None,
            "alerts_total": 0,
            "known_matches_total": 0,
            "latest_alert": None,
            "latest_candidates": [],
            "recent_alerts": [],
            "recent_known_matches": [],
        }
        if not output.exists() or json.loads(output.read_text(encoding="utf-8")) != payload:
            output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print("[INFO] Realtime source not configured; published waiting state.")
        return

    source = Path(realtime_source).expanduser().resolve()
    if not source.exists():
        payload = {
            "available": False,
            "source_updated_at": None,
            "last_frame": None,
            "latest_frame_image": None,
            "hunter_heartbeat_at": None,
            "hunter_status": None,
            "hunter_new_frames": None,
            "alerts_total": 0,
            "known_matches_total": 0,
            "latest_alert": None,
            "latest_candidates": [],
            "recent_alerts": [],
            "recent_known_matches": [],
        }
        output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"[INFO] Realtime source not present yet: {source}")
        return

    candidates_path = source / "latest_candidates.json"
    alerts_path = source / "alerts.tsv"
    known_path = source / "known_matches.tsv"
    frame_path = source / "last_detector_frame.txt"
    alert_text_path = source / "latest_alert.txt"
    heartbeat_path = source / "heartbeat.json"
    input_source_path = source / "input_source.json"

    latest_candidates = []
    if candidates_path.exists():
        try:
            loaded = json.loads(candidates_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                latest_candidates = loaded
        except Exception as exc:
            print(f"[WARN] Could not parse {candidates_path}: {exc}")

    # Preserve a bounded review history across frequent dashboard publishes.
    # Current candidates are inserted first, followed by older review rows from
    # the previous dashboard payload. Only the newest 100 unique reviews are
    # retained, so a candidate remains available long enough for human review.
    previous_payload = {}
    if output.exists():
        try:
            loaded = json.loads(output.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                previous_payload = loaded
        except Exception:
            previous_payload = {}

    history_seed = list(latest_candidates)
    old_history = previous_payload.get("review_candidates")
    if not isinstance(old_history, list):
        old_history = previous_payload.get("latest_candidates")
    if isinstance(old_history, list):
        history_seed.extend(old_history)

    review_history = []
    seen_reviews = set()
    for row in history_seed:
        if not isinstance(row, dict):
            continue
        review_id = clean(row.get("review_id")) or find_realtime_review_id(source, row)
        key = review_id or "|".join(
            str(row.get(k) or "") for k in ("candidate","first","last")
        )
        if not key or key in seen_reviews:
            continue
        seen_reviews.add(key)
        item = dict(row)
        if review_id:
            item["review_id"] = review_id
        review_history.append(item)
        if len(review_history) >= LIVE_MEDIA_LIMIT:
            break

    # Rebuild the ephemeral live-media staging area from the bounded history.
    # The live-media branch is force-replaced each publish, so it can never
    # grow beyond these retained review clips.
    shutil.rmtree(LIVE_MEDIA_STAGE, ignore_errors=True)
    LIVE_MEDIA_STAGE.mkdir(parents=True, exist_ok=True)
    review_candidates = [
        publish_realtime_review_assets(source, data_dir, row)
        for row in review_history
    ]

    by_review = {
        str(row.get("review_id")): row
        for row in review_candidates
        if row.get("review_id")
    }
    latest_candidates = [
        by_review.get(
            str(clean(row.get("review_id")) or find_realtime_review_id(source, row)),
            dict(row),
        )
        for row in latest_candidates
    ]

    alerts = read_tsv_records(alerts_path)
    known = read_tsv_records(known_path)

    last_frame = None
    if frame_path.exists():
        try:
            last_frame = frame_path.read_text(encoding="utf-8").strip() or None
        except Exception:
            pass

    # Display the official SOHO image directly instead of committing each
    # changing JPEG to Git. This keeps repository history from accumulating
    # one binary blob per processed frame.
    latest_frame_url = None
    if last_frame:
        match = re.fullmatch(
            r"(20\\d{6})_(\\d{4})_(c[23])_(512|1024)\\.jpg",
            last_frame,
            flags=re.IGNORECASE,
        )
        if match:
            day = match.group(1)
            camera = match.group(3).lower()
            latest_frame_url = (
                f"https://soho.nascom.nasa.gov/data/REPROCESSING/Completed/"
                f"{day[:4]}/{camera}/{day}/{last_frame}"
            )

    heartbeat = {}
    if heartbeat_path.exists():
        try:
            loaded = json.loads(heartbeat_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                heartbeat = loaded
        except Exception as exc:
            print(f"[WARN] Could not parse realtime heartbeat: {exc}")

    input_source = {}
    if input_source_path.exists():
        try:
            loaded = json.loads(input_source_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                input_source = loaded
        except Exception as exc:
            print(f"[WARN] Could not parse realtime input source: {exc}")

    # When the hunter is consuming SOHO near-realtime quicklook imagery, show
    # that same quicklook frame on the dashboard rather than pointing at a
    # completed/reprocessed JPEG that may not exist yet.
    if last_frame and input_source.get("active_source") == "near_realtime":
        gif_name = re.sub(r"_512\.jpg$", ".gif", last_frame, flags=re.IGNORECASE)
        latest_frame_url = (
            f"https://soho.nascom.nasa.gov/data/realtime/javagif/gifs/"
            f"{last_frame[:4]}/{gif_name}"
        )

    latest_alert = None
    if alert_text_path.exists():
        try:
            latest_alert = alert_text_path.read_text(encoding="utf-8", errors="replace").strip() or None
        except Exception:
            pass

    current_unmatched_total = sum(
        1 for row in latest_candidates
        if str(row.get("status") or "").upper() == "UNMATCHED"
    )
    current_known_total = sum(
        1 for row in latest_candidates
        if str(row.get("status") or "").upper() == "KNOWN_REPORT"
    )

    # Do not keep displaying an old red alert after its candidate has fallen
    # out of the latest detector pass.
    if latest_alert:
        alert_match = re.search(r"(?m)^Candidate:\s*(C\d+)\b", latest_alert, re.IGNORECASE)
        current_ids = {
            str(row.get("candidate") or "").upper()
            for row in latest_candidates
            if str(row.get("status") or "").upper() == "UNMATCHED"
        }
        if not alert_match or alert_match.group(1).upper() not in current_ids:
            latest_alert = None

    source_files = [
        p for p in (candidates_path, alerts_path, known_path, frame_path, alert_text_path, heartbeat_path, input_source_path)
        if p.exists()
    ]
    newest = max(source_files, key=lambda p: p.stat().st_mtime) if source_files else None

    payload = {
        "available": bool(source_files),
        "source_updated_at": file_iso(newest) if newest else None,
        "last_frame": last_frame,
        "latest_frame_image": latest_frame_url,
        "hunter_heartbeat_at": heartbeat.get("checked_at"),
        "hunter_status": heartbeat.get("status"),
        "hunter_new_frames": heartbeat.get("new_frames"),
        "active_input_source": input_source.get("active_source"),
        "input_preliminary": input_source.get("preliminary_input"),
        "near_realtime_latest": input_source.get("near_realtime_latest"),
        "completed_latest": input_source.get("completed_latest"),
        "completed_lag_minutes": input_source.get("completed_lag_minutes"),
        "current_unmatched_total": current_unmatched_total,
        "current_known_total": current_known_total,
        "alerts_total": len(alerts),
        "alerts_lifetime_total": len(alerts),
        "known_matches_total": len(known),
        "latest_alert": latest_alert,
        "latest_candidates": latest_candidates[:50],
        "review_candidates": review_candidates,
        "review_retention_limit": LIVE_MEDIA_LIMIT,
        "recent_alerts": alerts[-20:],
        "recent_known_matches": known[-20:],
    }

    previous = None
    if output.exists():
        try:
            previous = json.loads(output.read_text(encoding="utf-8"))
        except Exception:
            pass

    if previous != payload:
        output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"[OK] Wrote realtime dashboard data: {output}")
    else:
        print("[OK] Realtime dashboard data unchanged.")


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Update the comet dashboard from pipeline output.")
    parser.add_argument("--source", default=str(root / "results" / "visual"))
    parser.add_argument("--results-root", default=str(root / "results"))
    parser.add_argument("--no-copy-media", action="store_true")
    parser.add_argument(
        "--realtime-source",
        default=None,
        help="Optional results/realtime directory from the realtime hunter.",
    )
    parser.add_argument(
        "--realtime-promote-hours",
        type=float,
        default=24.0,
        help="Age after last observation before a realtime detection is added to the permanent candidate archive.",
    )
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    results_root = Path(args.results_root).expanduser().resolve()
    docs = root / "docs"
    data_dir = docs / "data"
    media_dir = docs / "media"
    data_dir.mkdir(parents=True, exist_ok=True)
    media_dir.mkdir(parents=True, exist_ok=True)

    if not source.exists():
        raise SystemExit(f"[ERROR] Source folder does not exist: {source}")

    folders = discover_candidate_folders(source)
    if not folders:
        raise SystemExit(f"[ERROR] No candidate MP4 files found under: {source}")

    records = load_metadata_records(results_root)
    merged_candidates = load_merged_candidates(results_root)
    event_classes = load_event_classes(results_root)
    events_by_id = load_events_by_id(results_root)
    print(f"[INFO] Metadata records available: {len(records)}")

    candidates = [
        build_candidate(folder, video, media_dir, not args.no_copy_media, records, merged_candidates, event_classes, events_by_id)
        for folder, video in folders
    ]

    # Old Gxxxx folders can be earlier visualisations of the same physical event.
    # Publish one card per event and prefer a stable EV... folder when available.
    deduped = {}
    for candidate in candidates:
        key = candidate.get("event_id") or candidate["id"]
        previous = deduped.get(key)
        if previous is None:
            deduped[key] = candidate
            continue
        candidate_is_ev = str(candidate["id"]).upper().startswith("EV")
        previous_is_ev = str(previous["id"]).upper().startswith("EV")
        candidate_score = candidate.get("score")
        previous_score = previous.get("score")
        if candidate_is_ev and not previous_is_ev:
            deduped[key] = candidate
        elif candidate_is_ev == previous_is_ev:
            try:
                if float(candidate_score) > float(previous_score):
                    deduped[key] = candidate
            except (TypeError, ValueError):
                pass
    candidates = list(deduped.values())

    realtime_archive = promote_realtime_candidates(
        args.realtime_source,
        data_dir,
        age_hours=args.realtime_promote_hours,
    )
    existing_ids = {str(c.get("id")) for c in candidates}
    for archived in realtime_archive:
        if str(archived.get("id")) not in existing_ids:
            candidates.append(archived)
            existing_ids.add(str(archived.get("id")))

    # Apply repository-backed human review overrides after deduplication so
    # known-object labels and permanent stars survive every dashboard publish.
    overrides_path = data_dir / "review_overrides.json"
    review_overrides = {}
    if overrides_path.exists():
        try:
            loaded = json.loads(overrides_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                review_overrides = {str(k).upper(): v for k, v in loaded.items()}
        except Exception as exc:
            print(f"[WARN] Could not parse {overrides_path}: {exc}")

    override_fields = (
        "review_state",
        "review_locked",
        "starred",
        "star_locked",
        "known_object",
    )
    applied_overrides = 0
    for candidate in candidates:
        event_key = str(candidate.get("event_id") or "").upper()
        id_key = str(candidate.get("id") or "").upper()
        override = review_overrides.get(event_key) or review_overrides.get(id_key)
        if not isinstance(override, dict):
            continue
        for field in override_fields:
            if field in override:
                candidate[field] = override[field]
        applied_overrides += 1

    if review_overrides:
        print(f"[INFO] Applied repository review overrides to {applied_overrides} candidate(s).")

    candidates_path = data_dir / "candidates.json"
    status_path = data_dir / "status.json"

    previous = {}
    if candidates_path.exists():
        try:
            previous = json.loads(candidates_path.read_text(encoding="utf-8"))
        except Exception:
            previous = {}

    if previous.get("candidates") == candidates and previous.get("demo") is False:
        payload = previous
        print("[OK] Candidate data unchanged; preserving existing dashboard timestamp.")
    else:
        payload = {"demo": False, "generated_at": now_iso(), "candidates": candidates}
        candidates_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        status = {
            "generated_at": payload["generated_at"],
            "candidate_count": len(candidates),
            "strong_review_count": sum(1 for c in candidates if c["review_class"] == "STRONG_REVIEW"),
            "secondary_count": sum(1 for c in candidates if c["review_class"] == "SECONDARY"),
        }
        status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")

    scored = sum(1 for c in candidates if c.get("score") is not None)
    classified = sum(1 for c in candidates if c.get("review_class") != "UNCLASSIFIED")

    print(f"[OK] Found {len(candidates)} candidate folder(s).")
    print(f"[OK] Matched pipeline score for {scored}/{len(candidates)} candidates.")
    print(f"[OK] Matched review class for {classified}/{len(candidates)} candidates.")
    print(f"[OK] Wrote {data_dir / 'candidates.json'}")
    print(f"[OK] Wrote {data_dir / 'status.json'}")

    publish_realtime_data(args.realtime_source, data_dir)
    update_realtime_html_fallback(data_dir)

if __name__ == "__main__":
    main()
