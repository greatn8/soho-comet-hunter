#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

VIDEO_PATTERNS = ("annotated*.mp4", "*.mp4")
IMAGE_PATTERNS = ("*.png", "*.jpg", "*.jpeg", "*.webp")
MAX_MEDIA_MB = 50

FIELD_ALIASES = {
    "score": ("score", "rank_score", "ranking_score", "event_score", "total_score"),
    "speed": ("speed", "track_speed", "speed_px_frame", "speed_px_per_frame", "velocity"),
    "sun_distance": ("sun", "sun_distance", "solar_distance", "sun_dist", "rho"),
    "rms": ("rms", "fit_rms", "track_rms", "residual_rms"),
    "frames": ("frames", "nframes", "frame_count", "num_frames"),
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
    found, seen = [], set()
    for pattern in VIDEO_PATTERNS:
        for video in source.rglob(pattern):
            folder = video.parent.resolve()
            if folder not in seen:
                seen.add(folder)
                found.append((folder, video))
        if found:
            break
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
                    token = token.strip()
                    if not token:
                        continue
                    # Store both the complete date:Cxx form and the Cxx suffix.
                    keys = {token.upper()}
                    if ":" in token:
                        keys.add(token.split(":")[-1].upper())
                    for key in keys:
                        by_member[key] = {
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

    print(f"[INFO] Loaded event classifications for {len(by_member)} member keys.")
    return by_member


def merged_group_id(candidate_id):
    return re.sub(r"_\d+$", "", str(candidate_id)).upper()


def event_for_merged(meta, event_classes):
    ids = []
    member_ids = clean(meta.get("member_ids"))
    candidate_id = clean(meta.get("candidate_id"))

    if member_ids:
        ids.extend(x.strip().upper() for x in member_ids.split(",") if x.strip())
    if candidate_id:
        ids.append(candidate_id.upper())

    for mid in ids:
        if mid in event_classes:
            return event_classes[mid]
        if ":" in mid and mid.split(":")[-1] in event_classes:
            return event_classes[mid.split(":")[-1]]
    return {}

def build_candidate(folder, video, media_dir, copy_media, records, merged_candidates, event_classes):
    json_meta = load_json_metadata(folder)
    candidate_id = str(json_meta.get("id") or folder.name)

    meta = pipeline_metadata(candidate_id, records)

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
        copied_video = safe_copy(video, out_dir / "annotated.mp4")
        if copied_video:
            video_url = f"./media/{clean_id}/{copied_video}"

        thumb = first_matching(folder, IMAGE_PATTERNS)
        if thumb:
            thumb_name = safe_copy(thumb, out_dir / f"preview{thumb.suffix.lower()}")
            if thumb_name:
                thumb_url = f"./media/{clean_id}/{thumb_name}"

    frames = meta.get("frames")
    if frames is None:
        n = count_frames(folder)
        frames = n if n else None

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
        "frames": frames,
        "notes": meta.get("notes") or "Automatically added from the comet-hunter results folder. Human review required.",
        "video": meta.get("video") or video_url,
        "thumbnail": meta.get("thumbnail") or thumb_url,
    }

def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Update the comet dashboard from pipeline output.")
    parser.add_argument("--source", default=str(root / "results" / "visual"))
    parser.add_argument("--results-root", default=str(root / "results"))
    parser.add_argument("--no-copy-media", action="store_true")
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
    print(f"[INFO] Metadata records available: {len(records)}")

    candidates = [
        build_candidate(folder, video, media_dir, not args.no_copy_media, records, merged_candidates, event_classes)
        for folder, video in folders
    ]

    payload = {"demo": False, "generated_at": now_iso(), "candidates": candidates}
    (data_dir / "candidates.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    status = {
        "generated_at": payload["generated_at"],
        "candidate_count": len(candidates),
        "strong_review_count": sum(1 for c in candidates if c["review_class"] == "STRONG_REVIEW"),
        "secondary_count": sum(1 for c in candidates if c["review_class"] == "SECONDARY"),
    }
    (data_dir / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")

    scored = sum(1 for c in candidates if c.get("score") is not None)
    classified = sum(1 for c in candidates if c.get("review_class") != "UNCLASSIFIED")

    print(f"[OK] Found {len(candidates)} candidate folder(s).")
    print(f"[OK] Matched pipeline score for {scored}/{len(candidates)} candidates.")
    print(f"[OK] Matched review class for {classified}/{len(candidates)} candidates.")
    print(f"[OK] Wrote {data_dir / 'candidates.json'}")
    print(f"[OK] Wrote {data_dir / 'status.json'}")

if __name__ == "__main__":
    main()
