#!/usr/bin/env python3
"""Upscale a JPEG, either with high-quality Lanczos resampling or an ML
super-resolution model.

Two modes:

  * default (Lanczos): fast, enlarges and visually cleans the image via
    interpolation. No new detail is invented.
  * --ml (EDSR super-resolution, via the `super-image` package): a neural
    network trained to add plausible high-frequency detail.

IMPORTANT — read before using this on license plates / small text:
Neither mode can *recover* detail the camera never captured. Lanczos only
interpolates. ML super-resolution does not reconstruct the truth either — it
*hallucinates* plausible-looking detail. On a plate that is only a few pixels
per character, the ML model will happily produce crisp-looking characters that
are NOT guaranteed to be the real ones. Do not trust any plate/number read from
an upscaled traffic-cam frame. Use this for visual quality, not forensics.

Usage:
    python3 upscale.py SRC DST [--factor 2] [--quality 95] [--no-sharpen]
    python3 upscale.py SRC DST --ml [--ml-scale 4] [--ml-model eugenesiow/edsr-base]
"""
import argparse
from PIL import Image, ImageFilter


def lanczos_upscale(im: Image.Image, factor: float, sharpen: bool) -> Image.Image:
    w, h = im.size
    new_size = (max(1, round(w * factor)), max(1, round(h * factor)))
    up = im.resize(new_size, Image.LANCZOS)
    if sharpen:
        up = up.filter(ImageFilter.UnsharpMask(radius=2, percent=120, threshold=2))
    return up


def _sr_one(model, ImageLoader, tile: Image.Image):
    """Run the SR model on a single (small) PIL tile -> PIL."""
    import numpy as np

    pred = model(ImageLoader.load_image(tile))
    arr = pred.squeeze(0).clamp(0, 1).mul(255).round().byte().permute(1, 2, 0).cpu().numpy()
    return Image.fromarray(arr.astype(np.uint8), "RGB")


def ml_upscale(im: Image.Image, scale: int, model_name: str, tile: int = 256,
               overlap: int = 16) -> Image.Image:
    """ML super-resolution via super-image (EDSR by default).

    Large images are processed in overlapping tiles so memory stays bounded
    (a full-frame SR pass otherwise allocates gigabytes and gets OOM-killed).

    Requires: pip install super-image torch
    """
    try:
        from super_image import EdsrModel, ImageLoader
    except ImportError as exc:  # pragma: no cover - dependency hint
        raise SystemExit(
            "ML upscaling needs the 'super-image' package (and torch):\n"
            "    pip install super-image torch\n"
            f"(import error: {exc})"
        )

    model = EdsrModel.from_pretrained(model_name, scale=scale)
    w, h = im.size

    # Small enough to do in one shot.
    if w <= tile and h <= tile:
        return _sr_one(model, ImageLoader, im)

    out = Image.new("RGB", (w * scale, h * scale))
    step = tile - overlap
    for y in range(0, h, step):
        for x in range(0, w, step):
            box = (x, y, min(x + tile, w), min(y + tile, h))
            piece = _sr_one(model, ImageLoader, im.crop(box))
            # Drop the overlap margin (except at the top/left edges) to avoid seams.
            ox = 0 if x == 0 else overlap * scale // 2
            oy = 0 if y == 0 else overlap * scale // 2
            paste = piece.crop((ox, oy, piece.width, piece.height))
            out.paste(paste, (box[0] * scale + ox, box[1] * scale + oy))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="source image path")
    ap.add_argument("dst", help="destination image path")
    ap.add_argument("--factor", type=float, default=2.0, help="Lanczos scale factor (default 2.0)")
    ap.add_argument("--quality", type=int, default=95, help="JPEG quality (default 95)")
    ap.add_argument("--no-sharpen", action="store_true", help="disable unsharp mask (Lanczos mode)")
    ap.add_argument("--ml", action="store_true", help="use ML super-resolution instead of Lanczos")
    ap.add_argument("--ml-scale", type=int, default=4, choices=[2, 3, 4],
                    help="ML upscale factor (default 4)")
    ap.add_argument("--ml-model", default="eugenesiow/edsr-base",
                    help="super-image model id (default eugenesiow/edsr-base)")
    ap.add_argument("--ml-tile", type=int, default=256,
                    help="ML tile size in px to bound memory (default 256)")
    args = ap.parse_args()

    im = Image.open(args.src).convert("RGB")
    w, h = im.size

    if args.ml:
        up = ml_upscale(im, args.ml_scale, args.ml_model, tile=args.ml_tile)
        mode = f"ML:{args.ml_model}@x{args.ml_scale}"
    else:
        up = lanczos_upscale(im, args.factor, not args.no_sharpen)
        mode = f"lanczos x{args.factor}{'' if args.no_sharpen else ' +sharpen'}"

    up.save(args.dst, "JPEG", quality=args.quality, optimize=True)
    print(f"{w}x{h} -> {up.size[0]}x{up.size[1]} q{args.quality} [{mode}]")


if __name__ == "__main__":
    main()
