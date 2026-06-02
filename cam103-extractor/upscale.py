#!/usr/bin/env python3
"""Upscale a JPEG using high-quality Lanczos resampling (+ optional sharpening).

Note: this enlarges and visually cleans up the image, but interpolation cannot
recover detail the camera never captured. For genuine detail reconstruction you
would need an ML super-resolution model (e.g. Real-ESRGAN), which is out of
scope here.

Usage:
    python3 upscale.py SRC DST [--factor 2] [--quality 95] [--no-sharpen]
"""
import argparse
from PIL import Image, ImageFilter


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src", help="source image path")
    ap.add_argument("dst", help="destination image path")
    ap.add_argument("--factor", type=float, default=2.0, help="scale factor (default 2.0)")
    ap.add_argument("--quality", type=int, default=95, help="JPEG quality (default 95)")
    ap.add_argument("--no-sharpen", action="store_true", help="disable unsharp mask")
    args = ap.parse_args()

    im = Image.open(args.src)
    im = im.convert("RGB")
    w, h = im.size
    new_size = (max(1, round(w * args.factor)), max(1, round(h * args.factor)))

    up = im.resize(new_size, Image.LANCZOS)
    if not args.no_sharpen:
        up = up.filter(ImageFilter.UnsharpMask(radius=2, percent=120, threshold=2))

    up.save(args.dst, "JPEG", quality=args.quality, optimize=True)
    print(f"{w}x{h} -> {new_size[0]}x{new_size[1]} q{args.quality}"
          f"{'' if args.no_sharpen else ' +sharpen'}")


if __name__ == "__main__":
    main()
