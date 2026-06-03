# Hyundai IONIQ 5 — visual signature for spotting in cam103

A field guide for telling a Hyundai IONIQ 5 apart from other cars, built from the
confirmed, freely-licensed reference photos in this folder. The last section is
tuned for the **cam103 traffic-camera view** (a high-mounted camera looking down
at the intersection from a steep angle, roughly 70–80° above the cars), which is
the hardest and most useful case.

> Body type: mid-size electric CUV / "kammback" hatch.
> Dimensions: **L ≈ 4635 mm, W ≈ 1890 mm, H ≈ 1605 mm, wheelbase ≈ 3000 mm.**
> The very long wheelbase with wheels pushed hard into the corners and tiny
> overhangs is itself a give-away — the footprint looks "stretched" for its
> roof length.

## The five tells that are unique to the IONIQ 5

These hold from almost any angle. If you see two or more, it is very likely an
IONIQ 5; the **Parametric Pixel lights** alone are near-conclusive.

1. **Parametric Pixel lights.** Head- and tail-lights are made of small,
   separated **square LED blocks** ("pixels"), not smooth lenses. The rear has a
   **full-width bar** of these pixels spanning the whole tailgate. No other
   mainstream car uses square-pixel clusters — this is the single best tell.
   See `rear_01..03`, `front_01..03`.
2. **Clean clamshell nose, no grille.** The front is a smooth, gently V-shaped
   panel (clamshell hood wrapping down) with **no radiator grille** and the
   pixel headlights set wide and low. A small Hyundai logo, no big chrome mouth.
3. **Z / parametric crease on the doors.** A strong angular character line kinks
   sharply along the lower doors — sharp folded surfaces, not soft curves.
   Visible in `side_01/02`, `threequarter_*`.
4. **Black wheel-arch cladding + pixel-pattern aero wheels.** Chunky black
   plastic cladding rings every wheel; the alloys have a distinctive
   turbine/"pixel" face. Reads as dark bands around the wheels even at distance.
5. **Kammback roofline.** Long, near-flat roof that ends in a clean vertical
   chop with a short roof spoiler — a stretched hot-hatch silhouette, not a
   tapering sedan tail and not a tall boxy SUV. Short front/rear overhangs.

## Quick angle key (which reference file to compare against)

| You can see…                    | Compare to            |
|---------------------------------|-----------------------|
| Nose / headlights only          | `front_01..03`        |
| Tail / full-width light bar     | `rear_01..03`         |
| Profile, doors, roofline        | `side_01`, `side_02`  |
| Front corner (most common)      | `front_left_*`, `front_right_*` |
| Rear corner                     | `rear_left_*`, `rear_right_*`   |
| Classic marketing 3/4           | `threequarter_01..06` |

## Telling it apart from common confusers

| Looks similar         | How the IONIQ 5 differs                                   |
|-----------------------|----------------------------------------------------------|
| **Kia EV6**           | EV6 has a *curved* coupe roofline and a *curved* rear light bar; IONIQ 5 is boxier with **square pixel** lights and a flat kammback roof. |
| **VW ID.4 / Skoda Enyaq** | Rounder, softer surfaces, smooth (non-pixel) lights, no clamshell crease. |
| **Chevrolet/Opel Bolt** (the recurring white car in the cam103 hunt) | Smaller footprint, rounded nose, conventional smooth headlights, **no** pixel lights, **no** black arch cladding band. |
| **VW Golf / generic hatch** | Much shorter — IONIQ 5's footprint is ~0.5 m longer and far wider; wheels at the extreme corners. |
| **Generic SUV/crossover** | Taller, longer roof, usually roof rails and a tapering/raked rear; IONIQ 5 roof is flat and ends in an abrupt vertical chop. |

## The cam103 view: steep top-down (~75° from ground), each side

cam103 is a 1280×720 frame from a high pole looking **down** onto the
intersection. You rarely get a clean side profile — instead you see **roof +
foreshortened nose/tail** and the car's **plan-view footprint**. The ground-level
references above don't show this directly, so use the derived cues below (the
roof/3-quarter shots `threequarter_*` and `side_02`, which is shot from a raised
sidewalk, are the closest real analogues).

What survives the steep angle and how to use it:

- **Rectangular, hard-cornered footprint.** From above the IONIQ 5 reads as a
  crisp **rectangle with sharp corners**, noticeably long for its width. Sedans
  show a tapering tail; rounded EVs (ID.4) show soft corners. The IONIQ 5 stays
  boxy.
- **Flat roof ending in a step.** The roof is long and flat, then drops in a
  short vertical face to a small spoiler. From a high rear-quarter angle you can
  often see that **chop + spoiler shelf** — unlike a sedan's continuous slope.
- **Pixel light bars are the night/low-light winner.** At dawn, dusk, rain or
  with lights on, the **full-width dotted bar** at the rear and the **two wide
  dotted blocks** at the front remain visible even when foreshortened — look for
  *dotted/segmented* light lines, not continuous ones.
- **Dark bands at all four wheels.** The black arch cladding still reads as four
  dark patches hugging the wheels, framing a lighter (often white/grey/matte)
  body. Combination of *light boxy body + four dark wheel corners + dotted
  lights* is a strong top-down signature.
- **Scale check.** Against lane markings/crosswalk, it sits between a normal
  hatch and a full SUV — long wheelbase makes the wheels look pushed to the very
  ends. Use the ~4.64 m length as a yardstick versus buses/trams/sedans in the
  same frame.

**Per-side note (left vs right approach):** the car is symmetric, so the same
cues apply mirrored. Whichever side faces the camera, anchor on (1) the dotted
light bar on the end you can see, (2) the flat roof + rear chop, and (3) the four
dark wheel-arch patches. Two of those three = call it an IONIQ 5 candidate and
zoom/upscale to confirm the **square pixels**.

## How this plugs into the toolkit

`detect.py` (YOLO) only knows the generic class **car** — it cannot tell an
IONIQ 5 from any other car. The intended workflow:

1. `detect.py` flags frames that contain `car` (narrows the haystack).
2. For each candidate, crop/zoom and (optionally) `upscale.py` the region.
3. Compare against the references here using the five tells, prioritising the
   **pixel lights** and **flat kammback roof + dark wheel corners** for the
   steep cam103 angle.

Treat upscaled detail with the caution noted in the main README — ML upscaling
can *invent* pixel-like artefacts, so confirm the **square** light blocks against
these real references rather than trusting an upscale alone.

See `manifest.json` for the source URL, author and licence of every image, and
`ATTRIBUTION.md` for the human-readable credits.
