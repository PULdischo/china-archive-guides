"""Command line: `uv run cah <step>` or `uv run cah all`."""

import argparse

from . import compare, export, geo, harvest, images, ocr


def main() -> None:
    ap = argparse.ArgumentParser(prog="cah", description=__doc__)
    ap.add_argument("step", choices=["harvest", "geo", "images", "ocr", "export", "compare", "all"])
    ap.add_argument("--engine", choices=list(ocr.ENGINES),
                    help="OCR engine for the ocr step (default: CAH_OCR_ENGINE or rapidocr)")
    ap.add_argument("--fallback-only", action="store_true",
                    help="ocr: only pages DashScope blocked, failed or hasn't done (use with --engine rapidocr)")
    args = ap.parse_args()

    steps = {
        "harvest": harvest.run,
        "geo": geo.run,
        "images": images.run,
        "ocr": lambda: ocr.run(args.engine, args.fallback_only),
        "export": export.run,
    }
    if args.step == "compare":
        compare.run()
        return
    for name, fn in steps.items():
        if args.step in (name, "all"):
            print(f"== {name}")
            fn()


if __name__ == "__main__":
    main()
