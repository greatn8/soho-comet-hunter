#!/usr/bin/env python3
"""
Build docs/data/candidates.json from comet-hunter output folders.

Default source folder:
    results/visual

Expected simple layout:
    results/visual/G0002_1024/annotated.mp4
    results/visual/G0010_1024/annotated.mp4

Optional metadata in each candidate folder:
    dashboard.json

Example dashboard.json:
{
  "title": "Candidate G0002",
  "confidence": 92.4,
  "family": "Kreutz",
  "status": "candidate",
  "source": "SOHO C2",
  "instrument": "LASCO C2",
  "first_seen": "2026-09-28T05:42:11Z",
  "frames": 11,
  "notes": "Faint coherent inbound track."
}

The script copies reasonably-sized videos/images into docs/media so GitHub
Pages can display them.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

VIDEO_PATTERNS = ("annotated*.mp4", "*.mp4")
IMAGE_PATTERNS = ("*.png", "*.jpg", "*.jpeg", "*.webp")
MAX_MEDIA_MB = 50


def iso_from_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def infer_source(path: Path) -> tuple[str, str]:
    text = str(path).lower()
    if "c2" in text:
        return "SOHO C2", "LASCO C2"
    if "c3" in text:
        return "SOHO C3", "LASCO C3"
    return "SOHO", "LASCO"


def infer_family(path: Path) -> str:
    text = str(path).lower()
    for family in ("kreutz", "meyer", "marsden", "kracht"):
        if family in text:
            return family.capitalize()
    return "Unknown"


def load_metadata(folder: Path) -> dict:
    for name in ("dashboard.json", "candidate.json"):
        p = folder / name
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"[WARN] Could not parse {p}: {exc}")
    return {}


def first_matching(folder: Path, patterns: tuple[str, ...]) -> Path | None:
    for pattern in patterns:
        matches = sorted(folder.glob(pattern))
        if matches:
            return matches[0]
    return None


def count_frames(folder: Path) -> int:
    exts = {".png", ".jpg", ".jpeg", ".webp", ".fits", ".fit"}
    return sum(1 for p in folder.iterdir() if p.is_file() and p.suffix.lower() in exts)


def safe_copy(source: Path, destination: Path) -> str | None:
    size_mb = source.stat().st_size / (1024 * 1024)
    if size_mb > MAX_MEDIA_MB:
        print(f"[WARN] Skipping {source.name}: {size_mb:.1f} MB exceeds {MAX_MEDIA_MB} MB dashboard limit.")
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination.name


def discover_candidate_folders(source: Path) -> list[tuple[Path, Path]]:
    found = []
    seen = set()

    for pattern in VIDEO_PATTERNS:
        for video in source.rglob(pattern):
            folder = video.parent.resolve()
            if folder not in seen:
                seen.add(folder)
                found.append((folder, video))
        if found:
            # annotated videos are preferred. Only fall back to all MP4 files
            # when no annotated files were found anywhere.
            break

    return sorted(found, key=lambda x: x[1].stat().st_mtime, reverse=True)


def build_candidate(folder: Path, video: Path, media_dir: Path, copy_media: bool) -> dict:
    meta = load_metadata(folder)
    candidate_id = str(meta.get("id") or folder.name)
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
        frame_count = count_frames(folder)
        frames = frame_count if frame_count else None

    return {
        "id": candidate_id,
        "title": meta.get("title") or f"Candidate {candidate_id}",
        "confidence": meta.get("confidence"),
        "family": meta.get("family") or infer_family(folder),
        "status": meta.get("status") or "candidate",
        "source": meta.get("source") or source_name,
        "instrument": meta.get("instrument") or instrument,
        "first_seen": meta.get("first_seen") or iso_from_mtime(video),
        "frames": frames,
        "notes": meta.get("notes") or "Automatically added from the comet-hunter results folder. Human review required.",
        "video": meta.get("video") or video_url,
        "thumbnail": meta.get("thumbnail") or thumb_url,
    }


def demo_payload() -> dict:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "demo": True,
        "generated_at": now,
        "candidates": [
            {
                "id": "DEMO-G0002",
                "title": "Demo Kreutz candidate",
                "confidence": 92.4,
                "family": "Kreutz",
                "status": "candidate",
                "source": "SOHO C2",
                "instrument": "LASCO C2",
                "first_seen": now,
                "frames": 11,
                "notes": "Demonstration card only. Replace this by running tools/update_dashboard.py against your real results.",
                "video": None,
                "thumbnail": None
            },
            {
                "id": "DEMO-G0010",
                "title": "Demo review candidate",
                "confidence": 76.8,
                "family": "Unknown",
                "status": "review",
                "source": "SOHO C3",
                "instrument": "LASCO C3",
                "first_seen": now,
                "frames": 8,
                "notes": "Demonstration card only. This is not an astronomical discovery claim.",
                "video": None,
                "thumbnail": None
            }
        ]
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]

    parser = argparse.ArgumentParser(description="Update the GitHub comet dashboard from result folders.")
    parser.add_argument(
        "--source",
        default=str(root / "results" / "visual"),
        help="Folder containing candidate result folders (default: results/visual)"
    )
    parser.add_argument(
        "--no-copy-media",
        action="store_true",
        help="Do not copy MP4/images into docs/media."
    )
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    docs = root / "docs"
    data_dir = docs / "data"
    media_dir = docs / "media"
    data_dir.mkdir(parents=True, exist_ok=True)
    media_dir.mkdir(parents=True, exist_ok=True)

    if not source.exists():
        print(f"[WARN] Source folder does not exist: {source}")
        print("[INFO] Keeping demo data so the dashboard still works.")
        payload = demo_payload()
    else:
        folders = discover_candidate_folders(source)
        if not folders:
            print(f"[WARN] No MP4 candidate outputs were found under: {source}")
            print("[INFO] Keeping demo data so the dashboard still works.")
            payload = demo_payload()
        else:
            candidates = [
                build_candidate(folder, video, media_dir, not args.no_copy_media)
                for folder, video in folders
            ]
            payload = {
                "demo": False,
                "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "candidates": candidates
            }
            print(f"[OK] Found {len(candidates)} candidate folder(s).")

    (data_dir / "candidates.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )

    status = {
        "generated_at": payload["generated_at"],
        "candidate_count": len(payload["candidates"]),
        "demo": payload["demo"]
    }
    (data_dir / "status.json").write_text(
        json.dumps(status, indent=2), encoding="utf-8"
    )

    print(f"[OK] Wrote {data_dir / 'candidates.json'}")
    print(f"[OK] Wrote {data_dir / 'status.json'}")
    if not args.no_copy_media:
        print(f"[OK] Dashboard media folder: {media_dir}")
    print("\nNext:")
    print("  git add docs tools")
    print('  git commit -m "Update comet dashboard"')
    print("  git push")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
