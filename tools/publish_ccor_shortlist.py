#!/usr/bin/env python3
"""Publish only shortlisted CCOR reviews, keeping the complete local detector output.

The local results/ccor/ccor.json remains the source of truth, including
non-shortlisted hypotheses. The public docs/data/ccor.json contains only
review_shortlist_ids and their associated track observations. No changes
are made to the realtime C3/alerts pipeline or the NOAA frame manifest.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def shortlisted_payload(source):
    """Return a new public payload; never modify the source in place."""
    if not isinstance(source, dict) or source.get("mode") != "EXPERIMENTAL_REVIEW_ONLY":
        raise ValueError("Not an experimental CCOR review payload")
    tracks = source.get("tracks")
    ids = source.get("review_shortlist_ids")
    if not isinstance(tracks, list) or not isinstance(ids, list):
        raise ValueError("CCOR tracks or review_shortlist_ids missing")
    if any(not isinstance(ident, str) or not ident for ident in ids):
        raise ValueError("Invalid shortlist ID")
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate shortlist IDs")

    index = {}
    for track in tracks:
        if not isinstance(track, dict) or not isinstance(track.get("id"), str):
            raise ValueError("Invalid CCOR track")
        if track["id"] in index:
            raise ValueError("Duplicate CCOR track ID")
        index[track["id"]] = track
    missing = [ident for ident in ids if ident not in index]
    if missing:
        raise ValueError("Shortlist IDs missing from retained tracks: " + ", ".join(missing[:5]))

    shortlisted = [index[ident] for ident in ids]
    selected = set(ids)
    result = dict(source)
    result["tracks"] = shortlisted
    result["temporal_overlaps_not_duplicates"] = [
        pair for pair in source.get("temporal_overlaps_not_duplicates", [])
        if isinstance(pair, list) and len(pair) == 2
        and pair[0] in selected and pair[1] in selected
    ]
    result["publication_policy"] = "SHORTLIST_ONLY"
    result["local_retained_track_count"] = len(tracks)
    result["published_review_track_count"] = len(shortlisted)
    result["published_per_camera"] = {
        camera: sum(track.get("camera") == camera for track in shortlisted)
        for camera in ("ccor1", "ccor2")
    }
    return result


def publish(source_file, destination_file):
    source_file = Path(source_file)
    destination_file = Path(destination_file)
    payload = shortlisted_payload(json.loads(source_file.read_text(encoding="utf-8")))
    destination_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination_file.with_name(destination_file.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination_file)
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        payload = publish(args.source, args.destination)
    except (OSError, TypeError, ValueError) as exc:
        print("[WARN] Not publishing invalid CCOR shortlist: " + str(exc)[:250])
        return
    print("[OK] CCOR shortlist-only publication: " +
          str(payload["published_review_track_count"]) + " public reviews; " +
          str(payload["local_retained_track_count"]) + " retained locally")


if __name__ == "__main__":
    main()
