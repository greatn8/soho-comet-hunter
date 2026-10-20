#!/usr/bin/env python3
"""Inventory *independently labelled* real known-comet training sets.

The Sungrazer Project official comet-hunting guide provides six practice
archives containing C2/C3 image sequences plus human-recorded comet positions:
  https://sungrazer.nrl.navy.mil/index.php/soho_guide

This command inventories their contents rather than pretending an arbitrary
candidate in an already-known time window is the actual known comet.

Default: NO NETWORK. --fetch downloads ONE opt-in archive from Sungrazer
(not SOHO Completed); --archive works with a manually downloaded local ZIP.
NO realtime service or CUDA modifications; no file extraction and no imaging
recompression. It deliberately does not guess the internal labels' format.
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import shutil
import urllib.error
import urllib.request
import zipfile

BASE = "https://sungrazer.nrl.navy.mil/sites/sungrazer/files/images/sets/"
DATASETS = ("soho732", "soho1264", "soho1312", "soho1469", "soho1510", "soho1561")
MAX_BYTES = 60 * 1024 * 1024
TEXT_EXT = {".txt", ".csv", ".dat", ".md", ".text"}
IMAGE_EXT = {".gif", ".jpg", ".jpeg", ".png", ".fits", ".fit", ".fts"}


def inspect_archive(path: Path):
    with zipfile.ZipFile(path) as z:
        entries = [entry for entry in z.infolist() if not entry.is_dir()]
        images = [m for m in entries if Path(m.filename).suffix.lower() in IMAGE_EXT]
        texts = [m for m in entries if Path(m.filename).suffix.lower() in TEXT_EXT]
        sizes = Counter(Path(m.filename).suffix.lower() for m in entries)
        print("Archive:", path)
        print("Members:", len(entries), "Images:", len(images),
              "Text/position candidates:", len(texts))
        print("Extensions:", dict(sorted(sizes.items())))
        print("First 25 image paths:")
        for member in images[:25]:
            print(" ", member.filename, f"({member.file_size:,} bytes)")
        if len(images)>25:
            print(" ... additional image paths:", len(images)-25)
        print("Position/metadata text files:")
        for member in texts[:30]:
            print(" ", member.filename, f"({member.file_size:,} bytes)")
            if member.file_size <= 150000:
                try:
                    with z.open(member) as f:
                        lines=f.read(min(member.file_size,8000)).decode("utf-8","replace")
                    print("   BEGIN TEXT SAMPLE")
                    for line in lines.splitlines()[:60]:
                        print("   "+line[:240])
                    print("   END TEXT SAMPLE")
                except Exception as error:
                    print("   Could not preview text:", error)
        print("These are inventory findings, NOT confirmed recovered tracks.")


def fetch_archive(dataset: str, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and zipfile.is_zipfile(destination):
        print("Using already-downloaded official training ZIP:",destination)
        return destination
    request=urllib.request.Request(
        BASE+dataset+".zip",
        headers={"User-Agent":"SOHO-Comet-Hunter-Scientific-Benchmark/1.0"}
    )
    temp=destination.with_suffix(".downloading")
    total=0
    try:
        with urllib.request.urlopen(request,timeout=60) as response, temp.open("wb") as dst:
            while True:
                chunk=response.read(1024*1024)
                if not chunk:break
                total+=len(chunk)
                if total>MAX_BYTES:
                    raise RuntimeError("Training archive larger than 60 MB safety limit")
                dst.write(chunk)
        if not zipfile.is_zipfile(temp):
            raise RuntimeError("Downloaded content is not a ZIP (possible 403 or HTML)")
        temp.replace(destination)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    print("Downloaded:",destination, f"({total:,} bytes)")
    return destination


def main():
    ap=argparse.ArgumentParser(description=__doc__,
                               formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project",type=Path,required=True)
    ap.add_argument("--dataset",choices=DATASETS,default="soho1561")
    ap.add_argument("--archive",type=Path,
                    help="Use manually downloaded training ZIP; NO network")
    ap.add_argument("--fetch",action="store_true",
                    help="Download exactly one archive from Sungrazer (not SOHO)")
    args=ap.parse_args()
    if args.archive and args.fetch:
        ap.error("Specify --archive or --fetch, not both")
    dest=(args.project.expanduser().resolve()/"results"/
          "official_sungrazer_controls"/(args.dataset+".zip"))
    if args.archive:
        archive=args.archive.expanduser().resolve()
    elif args.fetch:
        try:
            archive=fetch_archive(args.dataset,dest)
        except Exception as error:
            raise SystemExit(
                f"Could not download the official Sungrazer practice ZIP: {error}\n"
                "The official website may block direct scripted access. "
                "You can download the linked ZIP in your browser and re-run "
                "with --archive /path/to/soho1561.zip. "
                "No realtime data or detector was changed."
            )
    elif dest.exists():
        archive=dest
    else:
        print("PLAN ONLY. Official Sungrazer comet training sets:")
        for name in DATASETS:
            print(f"  {name}: {BASE}{name}.zip")
        print("Use --fetch --dataset soho1561 for one 2 MB-ish official ZIP, "
              "or --archive /path/to/downloaded.zip.")
        return
    if not archive.is_file() or not zipfile.is_zipfile(archive):
        ap.error("Training ZIP missing, inaccessible, or invalid: "+str(archive))
    inspect_archive(archive)


if __name__ == "__main__":
    main()
