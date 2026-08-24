#!/usr/bin/env python3
# index every id you have already carded into known_ids.txt; grepping a rendered hub for URLs is how this fleet once re-staged a carded video and overwrote it.
"""Print source video entries whose normalized IDs are absent from a known-id list."""
import argparse
import os
import re
import sys
from urllib.parse import parse_qs, urlparse


ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def normalize_id(value):
    """Return a YouTube ID from a supported URL or bare 11-character ID, else None."""
    value = value.strip()
    if ID_RE.fullmatch(value):
        return value
    parsed = urlparse(value)
    if parsed.netloc.lower().endswith("youtu.be"):
        candidate = parsed.path.strip("/").split("/", 1)[0]
        return candidate if ID_RE.fullmatch(candidate) else None
    candidate = parse_qs(parsed.query).get("v", [None])[0]
    return candidate if candidate and ID_RE.fullmatch(candidate) else None


def read_known(path):
    known = set()
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            candidate = normalize_id(line.split("|", 1)[0])
            if candidate:
                known.add(candidate)
    return known


def read_source(path, known):
    emitted = set()
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            raw_id, separator, title = line.partition("|")
            video_id = normalize_id(raw_id)
            if video_id and video_id not in known and video_id not in emitted:
                rows.append(f"{video_id}|{title.strip()}\n")
                emitted.add(video_id)
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--known", required=True, help="one known id or URL per line")
    parser.add_argument("--source", required=True, help="one new id|title, URL, or ID per line")
    parser.add_argument("--out", help="write output here instead of stdout")
    args = parser.parse_args(argv)
    for path in (args.known, args.source):
        if not os.path.isfile(path):
            print(f"error: missing input file: {path}", file=sys.stderr)
            return 1
    rows = read_source(args.source, read_known(args.known))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.writelines(rows)
    else:
        sys.stdout.writelines(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
