#!/usr/bin/env python3
"""Watch a fixed spot in a public camera snapshot and report when a
light-coloured (e.g. white) vehicle leaves it.

Designed for GitHub Actions. Behaviour:
  * If START_LOCAL ("HH:MM", Europe/Tallinn by default) is set and still in
    the future today, wait until then; otherwise start immediately.
  * Baseline-on-start: whatever occupies the spot when the sweep starts is the
    reference (a bright/white vehicle gives a high white-pixel fraction).
  * Every INTERVAL seconds, re-check. If the white-pixel fraction in the spot
    falls below THR_RATIO x baseline for MISS_N consecutive checks, declare the
    vehicle GONE, save evidence, and exit.
  * Stops after MAX_MIN minutes if nothing leaves.

Env: CAM_URL, SPOT_BOX="x1,y1,x2,y2", START_LOCAL, TZ_OFFSET (hours, default 3),
     INTERVAL (s), MAX_MIN (min), THR_RATIO, MISS_N, LABEL, OUT.
Writes evidence to OUT/ and a `gone=` line to $GITHUB_OUTPUT.
"""
import os, io, sys, time, json, calendar, urllib.request
from PIL import Image
import numpy as np

CAM_URL   = os.environ.get("CAM_URL", "https://ristmikud.tallinn.ee/last/cam021.jpg")
BOX       = tuple(int(x) for x in os.environ.get("SPOT_BOX", "110,408,512,578").split(","))
START     = os.environ.get("START_LOCAL", "").strip()
TZ_OFFSET = int(os.environ.get("TZ_OFFSET", "3"))
INTERVAL  = int(os.environ.get("INTERVAL", "300"))
MAX_MIN   = int(os.environ.get("MAX_MIN", "300"))
THR_RATIO = float(os.environ.get("THR_RATIO", "0.6"))
MISS_N    = int(os.environ.get("MISS_N", "2"))
LABEL     = os.environ.get("LABEL", "white Skoda Fabia (black roof)")
OUT       = os.environ.get("OUT", "watch_out")
HDR       = {"User-Agent": "Mozilla/5.0"}

os.makedirs(OUT, exist_ok=True)

def log(m): print(m, flush=True)

def grab():
    data = urllib.request.urlopen(urllib.request.Request(CAM_URL, headers=HDR), timeout=20).read()
    return Image.open(io.BytesIO(data)).convert("RGB")

def wfrac(img):
    a = np.asarray(img, dtype=float)
    lum = a.mean(2); sat = a.max(2) - a.min(2)
    return float(((lum > 150) & (sat < 35)).mean())

# ---- optional wait until designated local time ----
if START:
    hh, mm = (int(x) for x in START.split(":"))
    now = time.time()
    lt = time.gmtime(now + TZ_OFFSET * 3600)
    target = calendar.timegm((lt.tm_year, lt.tm_mon, lt.tm_mday, hh, mm, 0, 0, 0, 0)) - TZ_OFFSET * 3600
    if target < now - 60:
        log("Designated time %s local already passed today -> starting immediately." % START)
    else:
        log("Waiting %d s until %s local before sweeping..." % (int(target - now), START))
        while time.time() < target:
            time.sleep(min(30, max(1, target - time.time())))
        log("Reached start time.")
else:
    log("No start time given -> sweeping immediately.")

# ---- baseline-on-start ----
im = grab()
base_wf = wfrac(im.crop(BOX))
im.crop(BOX).save(os.path.join(OUT, "baseline_spot.jpg"))
im.save(os.path.join(OUT, "baseline_full.jpg"))
thr = base_wf * THR_RATIO
log("Baseline white_frac=%.3f thr=%.3f box=%s label='%s'" % (base_wf, thr, BOX, LABEL))
if base_wf < 0.05:
    log("WARNING: baseline signal very low; the target may not be in the spot at start.")

deadline = time.time() + MAX_MIN * 60
miss = 0
result = {"gone": False, "label": LABEL, "box": list(BOX), "baseline_wf": round(base_wf, 3)}

while time.time() < deadline:
    time.sleep(INTERVAL)
    try:
        im = grab()
    except Exception as e:
        log("fetch-error: %s" % e); continue
    wf = wfrac(im.crop(BOX))
    ts = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    if wf < thr:
        miss += 1
        log("low-signal %s wf=%.3f (%d/%d)" % (ts, wf, miss, MISS_N))
        if miss >= MISS_N:
            im.crop(BOX).save(os.path.join(OUT, "gone_spot.jpg"))
            im.save(os.path.join(OUT, "gone_full.jpg"))
            result.update({"gone": True, "gone_at_utc": ts, "wf": round(wf, 3)})
            log("RESULT: GONE - %s left the spot at %s (wf=%.3f < thr=%.3f)" % (LABEL, ts, wf, thr))
            break
    else:
        miss = 0
        log("present %s wf=%.3f" % (ts, wf))

if not result["gone"]:
    log("RESULT: STILL-PRESENT - watch ended after %d min with no departure." % MAX_MIN)

with open(os.path.join(OUT, "result.json"), "w") as f:
    json.dump(result, f)

gh_out = os.environ.get("GITHUB_OUTPUT")
if gh_out:
    with open(gh_out, "a") as f:
        f.write("gone=%s\n" % ("true" if result["gone"] else "false"))
        f.write("gone_at=%s\n" % result.get("gone_at_utc", ""))
