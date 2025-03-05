#!/usr/bin/env python3

from __future__ import annotations

import glob
import re
import sys

XEMU_VERSION_RE = re.compile(r"results/xemu-(\d+)\.(\d+)\.(\d+)-master.*")

def main() -> int:
    results = glob.glob("results/*")

    latest_version: tuple[int, int, int] = (0, 0, 0)
    latest_path = ""

    for result in results:
        match = XEMU_VERSION_RE.match(result)
        if not match:
            continue

        version = int(match.group(1)), int(match.group(2)), int(match.group(3))

        if not latest_path:
            latest_version = version
            latest_path = result
            continue

        major = version[0] - latest_version[0]
        minor = version[1] - latest_version[1]
        patch = version[2] - latest_version[2]

        if major > 0:
            latest_version = version
            latest_path = result
            continue
        if major < 0:
            continue

        if minor > 0:
            latest_version = version
            latest_path = result
            continue
        if minor < 0:
            continue

        if patch > 0:
            latest_version = version
            latest_path = result

    if not latest_path:
        return 1

    print(latest_path)
    return 0

if __name__ == "__main__":
    sys.exit(main())