# IONIQ 5 reference library

A curated set of **confirmed Hyundai IONIQ 5 photos** (from Wikimedia Commons,
freely licensed) to help distinguish an IONIQ 5 from other cars when reviewing
cam103 frames — and to find one in the live feed.

## Contents

- **23 images, 8 angles** — `front`, `rear`, `front_left`, `front_right`,
  `rear_left`, `rear_right`, `side`, and `threequarter` (classic marketing 3/4).
  Includes `lights_on_01.jpg` — a front_right shot with the **pixel DRLs lit**,
  the clearest illuminated-light signature for low-light cam frames.
- **`IONIQ5_SIGNATURE.md`** — the field guide: the five tells unique to the
  IONIQ 5, a confuser table (EV6, ID.4, Bolt, generic hatch/SUV), and a section
  deriving the **steep ~75° top-down cam103 view** from each side.
- **`manifest.json`** — machine-readable: file → angle, Commons title, source
  URL, author, licence.
- **`ATTRIBUTION.md`** — human-readable credits (required for the CC BY-SA
  images).

## Why no true top-down photos

cam103 looks down on the intersection at roughly 70–80° from the ground.
Confirmed top-down/aerial IONIQ 5 photos essentially don't exist on Wikimedia
Commons, so the steep-angle appearance is **derived** in `IONIQ5_SIGNATURE.md`
from the roof, three-quarter and raised-vantage shots rather than shown
directly. The closest real analogue in the set is `side_02` (shot from a raised
sidewalk).

## Refreshing / extending the set

The images were pulled from `Category:Hyundai Ioniq 5` on Wikimedia Commons via
the MediaWiki API, downscaled to 1100 px wide. To add angles or variants, query
that category, download originals, and append to `manifest.json` with the source
and licence. Keep the per-image attribution intact for any CC BY-SA file.

> Licences: CC0 (public domain) and CC BY-SA 3.0/4.0. See `ATTRIBUTION.md`.
