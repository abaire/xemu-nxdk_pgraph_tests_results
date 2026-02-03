#!/usr/bin/env python3

from __future__ import annotations

import glob
import os
import re
import sys

XEMU_VERSION_RE = re.compile(r"results/xemu-(\d+)\.(\d+)\.(\d+)-master.*")


def main() -> int:
    results = glob.glob("results/*")

    # List of (version_tuple, mtime, path)
    candidates = []

    for result in results:
        match = XEMU_VERSION_RE.match(result)
        if not match:
            continue

        version = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        try:
            mtime = os.path.getmtime(result)
        except OSError:
            mtime = 0.0

        candidates.append({'version': version, 'mtime': mtime, 'path': result})

    if not candidates:
        return 1

    # Sort by version (descending), then mtime (descending)
    # Python's sort is stable, but we can just use a key tuple.
    # We want max version, max mtime.
    # sorted(..., reverse=True) will sort by first element desc, then second desc.
    candidates.sort(key=lambda x: (x['version'], x['mtime']), reverse=True)

    print(candidates[0]['path'])
    return 0


if __name__ == "__main__":
    sys.exit(main())
