#!/bin/env python3

from __future__ import annotations

import argparse
import glob
import logging
import os
import sys

from PIL import Image

logger = logging.getLogger(__name__)


def _find_images(results_dir: str) -> set[str]:
    return set(glob.glob("**/*.png", root_dir=results_dir, recursive=True))


def _needs_processing(image_path: str, output_receipt: str) -> bool:
    try:
        mtime1 = os.path.getmtime(image_path)
        mtime2 = os.path.getmtime(output_receipt)
        return mtime1 > mtime2
    except OSError:
        return False


def _touch(file_path: str) -> None:
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    try:
        os.utime(file_path, None)
    except OSError:
        open(file_path, 'a').close()


def generate_no_alpha_image(image_path: str, output_image: str, output_receipt: str) -> None:
    """Possibly clones the source_image into output_image with alpha removed. Touches the output_receipt file."""
    if not _needs_processing(image_path, output_receipt):
        return

    img = Image.open(image_path)

    _touch(output_receipt)

    def may_have_transparency():
        return img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info)

    if not may_have_transparency():
        return

    def has_transparency():
        if img.mode == "P":
            transparent = img.info.get("transparency", -1)
            for _, index in img.getcolors():
                if index == transparent:
                    return True
            return False

        if img.mode == "RGBA":
            extrema = img.getextrema()
            if extrema[3][0] < 255:
                return True

        if img.mode == "LA":
            extrema = img.getextrema()
            if extrema[1][0] < 255:
                return True

        return False

    if has_transparency():
        logger.debug("Generating no-alpha image '%s'", output_image)
        img = img.convert('RGB')
        img.save(output_image)


def generate_missing_no_alpha_images(results_dir: str, output_dir: str) -> None:
    logger.info("Generating no-alpha images for '%s' into '%s'", results_dir, output_dir)

    for img in _find_images(results_dir):
        img_path = os.path.join(results_dir, img)
        output_image = os.path.join(output_dir, img_path)

        if os.path.isfile(output_image):
            continue

        receipt = os.path.join(output_dir, results_dir, f"{img}.checked")
        if os.path.isfile(receipt):
            continue

        generate_no_alpha_image(img_path, output_image, receipt)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--verbose",
        "-v",
        help="Enables verbose logging information",
        action="store_true",
    )
    parser.add_argument(
        "--results-dir",
        default="results",
        help="Directory including test outputs that will be processed",
    )
    parser.add_argument(
        "--output-dir",
        default="no-alpha",
        help="Directory into which stripped alpha files will be generated",
    )

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level)

    generate_missing_no_alpha_images(args.results_dir, args.output_dir)

    return 0


if __name__ == "__main__":
    sys.exit(main())
