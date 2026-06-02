#!/usr/bin/env python3
"""Count persons, cars and buses in an image using a YOLO object detector.

Uses Ultralytics YOLO (COCO-pretrained). The first run downloads the model
weights (~6 MB for yolov8n) automatically.

Usage:
    python3 detect.py IMAGE [--model yolov8n.pt] [--conf 0.25] [--save out.jpg]
    python3 detect.py --url https://ristmikud.tallinn.ee/last/cam103.jpg
    python3 detect.py --cam cam103

Examples:
    python3 detect.py frame.jpg --save annotated.jpg
"""
import argparse
import sys
import tempfile
import urllib.request

# COCO class ids we care about.
TARGETS = {0: "person", 2: "car", 5: "bus"}
# Extras worth reporting (buses/trucks and bikes are easy to confuse).
EXTRAS = {3: "motorcycle", 7: "truck"}


def fetch(url: str) -> str:
    path = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False).name
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r, open(path, "wb") as f:
        f.write(r.read())
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("image", nargs="?", help="path to an image file")
    src.add_argument("--url", help="download and analyze this image URL")
    src.add_argument("--cam", help="camera id, e.g. cam103 (uses the Tallinn feed)")
    ap.add_argument("--model", default="yolov8n.pt", help="YOLO weights (default yolov8n.pt)")
    ap.add_argument("--conf", type=float, default=0.25, help="confidence threshold")
    ap.add_argument("--save", metavar="OUT", help="write an annotated image to OUT")
    ap.add_argument("--format", choices=("pretty", "csv", "json"), default="pretty",
                    help="output format: pretty (default), csv (person,car,bus), json")
    args = ap.parse_args()

    if args.cam:
        image = fetch(f"https://ristmikud.tallinn.ee/last/{args.cam}.jpg")
    elif args.url:
        image = fetch(args.url)
    else:
        image = args.image

    try:
        from ultralytics import YOLO
    except ImportError:
        print("Ultralytics not installed. Run: pip install ultralytics", file=sys.stderr)
        return 2

    # In machine-readable modes keep stdout clean: route model download /
    # loading chatter to stderr so only the csv/json line lands on stdout.
    import contextlib
    sink = sys.stderr if args.format != "pretty" else sys.stdout
    with contextlib.redirect_stdout(sink):
        model = YOLO(args.model)
        results = model.predict(image, conf=args.conf, verbose=False)
    r = results[0]

    counts = {name: 0 for name in TARGETS.values()}
    extras = {name: 0 for name in EXTRAS.values()}
    for cls in r.boxes.cls.tolist():
        cid = int(cls)
        if cid in TARGETS:
            counts[TARGETS[cid]] += 1
        elif cid in EXTRAS:
            extras[EXTRAS[cid]] += 1

    if args.format == "csv":
        # person,car,bus — machine-readable for logging/scripts.
        print(f"{counts['person']},{counts['car']},{counts['bus']}")
    elif args.format == "json":
        import json
        print(json.dumps({**counts, **{k: v for k, v in extras.items() if v}}))
    else:
        print(f"Image: {image}")
        print(f"Model: {args.model}  conf>={args.conf}")
        print("-" * 32)
        for name in ("person", "car", "bus"):
            print(f"  {name:<8}: {counts[name]}")
        if any(extras.values()):
            print("  (also seen)")
            for name, n in extras.items():
                if n:
                    print(f"  {name:<8}: {n}")

    if args.save:
        r.save(filename=args.save)
        if args.format == "pretty":
            print(f"\nAnnotated image written to {args.save}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
