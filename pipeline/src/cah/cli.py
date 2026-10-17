"""Command line: `uv run cah <step>` or `uv run cah all`."""

import argparse

from . import audit, compare, csindex, export, geo, harvest, images, ocr, release, rescue, search, wenshi


def main() -> None:
    ap = argparse.ArgumentParser(prog="cah", description=__doc__)
    ap.add_argument("step", choices=["harvest", "geo", "images", "ocr", "export", "search", "compare", "wenshi-test", "release", "publish", "rescue", "csindex", "audit", "all"])
    ap.add_argument("--engine", choices=list(ocr.ENGINES),
                    help="OCR engine for the ocr step (default: CAH_OCR_ENGINE or rapidocr)")
    ap.add_argument("--fallback-only", action="store_true",
                    help="ocr: only pages DashScope blocked, failed or hasn't done (use with --engine rapidocr)")
    ap.add_argument("--repo", default=release.DEFAULT_REPO, help="release/publish: Hugging Face dataset repo")
    ap.add_argument("--private", action="store_true", help="publish: create the dataset repo as private")
    ap.add_argument("--baseline", type=int, default=0, help="audit: run RapidOCR on N sampled pages first")
    ap.add_argument("--sample", type=int, default=0, help="audit: write a human-review sheet of N pages")
    ap.add_argument("--site-url", default="https://cozy-fox-6aa1e3.netlify.app",
                    help="audit: site base URL for review links")
    args = ap.parse_args()

    steps = {
        "harvest": harvest.run,
        "geo": geo.run,
        "images": images.run,
        "ocr": lambda: ocr.run(args.engine, args.fallback_only),
        "export": export.run,
        "search": search.run,
        "csindex": csindex.run,
    }
    if args.step == "compare":
        compare.run()
        return
    if args.step == "audit":
        audit.run(args.baseline, args.sample, args.site_url)
        return
    if args.step == "rescue":
        rescue.run()
        return
    if args.step == "release":
        release.run(args.repo)
        return
    if args.step == "publish":
        release.publish(args.repo, args.private)
        return
    if args.step == "wenshi-test":
        wenshi.run_test()
        return
    for name, fn in steps.items():
        if args.step in (name, "all"):
            print(f"== {name}")
            fn()


if __name__ == "__main__":
    main()
