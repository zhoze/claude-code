#!/usr/bin/env python3
"""Capture snapshots from a Viimsi (kaamerad.viimsivald.ee) camera.

The site is WordPress; the browser pulls each snapshot by POSTing to
admin-ajax.php with action=get_updated_(digest_)camera_image, and the
server proxies the camera and returns JSON {"image": <data-uri|url>}.
This must run from an Estonian IP (the AJAX endpoint is geo-restricted).
"""
import sys, os, re, json, time, base64, urllib.request, urllib.parse

PAGE = os.environ.get("CAM_PAGE", "https://kaamerad.viimsivald.ee/camera/parnamae-parkla-2/")
AJAX = "https://kaamerad.viimsivald.ee/wp-admin/admin-ajax.php"
SLUG = os.environ.get("CAM_SLUG", "parnamae-parkla-2")
OUT = os.environ.get("CAM_OUT", "frames")
FRAMES = int(os.environ.get("CAM_FRAMES", "5"))
INTERVAL = int(os.environ.get("CAM_INTERVAL", "8"))
UA = {"User-Agent": "Mozilla/5.0"}


def fetch(url, data=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout).read()


def main():
    os.makedirs(OUT, exist_ok=True)
    html = fetch(PAGE, timeout=25).decode("utf-8", "ignore")

    def attr(name):
        m = re.search(name + r'="([^"]*)"', html)
        return m.group(1) if m else ""

    img, cid, auth = attr("data-image"), attr("data-id"), attr("data-auth")
    if not img or not cid:
        print("::error::could not parse camera params from page")
        sys.exit(1)
    action = "get_updated_digest_camera_image" if auth == "digest" else "get_updated_camera_image"
    print("camera id=%s auth=%s action=%s" % (cid, auth, action))

    ok = 0
    for i in range(1, FRAMES + 1):
        ts = time.strftime("%H%M%S", time.gmtime())
        body = urllib.parse.urlencode({"action": action, "url": img, "id": cid}).encode()
        try:
            resp = fetch(AJAX, data=body, timeout=30).decode("utf-8", "ignore")
        except Exception as e:
            print("frame %d -> ajax failed: %s" % (i, e)); time.sleep(INTERVAL); continue
        try:
            image = json.loads(resp).get("image", "")
        except Exception:
            print("frame %d -> non-JSON response head: %r" % (i, resp[:100])); time.sleep(INTERVAL); continue
        if not image or image == "not ok":
            print("frame %d -> no image (response head: %r)" % (i, resp[:100])); time.sleep(INTERVAL); continue
        out = os.path.join(OUT, "%s_%s.jpg" % (SLUG, ts))
        try:
            if image.startswith("data:"):
                with open(out, "wb") as f:
                    f.write(base64.b64decode(image.split(",", 1)[1]))
            elif image.startswith("http"):
                with open(out, "wb") as f:
                    f.write(fetch(image, timeout=20))
            else:
                print("frame %d -> unknown image format: %r" % (i, image[:60])); time.sleep(INTERVAL); continue
            sz = os.path.getsize(out)
            print("frame %d -> %d bytes" % (i, sz))
            if sz > 5000:
                ok += 1
        except Exception as e:
            print("frame %d -> save failed: %s" % (i, e))
        time.sleep(INTERVAL)

    print("valid frames: %d" % ok)
    if ok < 1:
        print("::error::no valid camera frames captured")
        sys.exit(1)


if __name__ == "__main__":
    main()
