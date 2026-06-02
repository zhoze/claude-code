# cam103 photo extractor

Captures the current full-resolution frame from a Tallinn traffic camera and
uploads it to a Google Drive folder using [rclone](https://rclone.org/).

Source: <https://ristmikud.tallinn.ee/index.php/cams> — each camera is served as
a single latest-frame JPEG at `https://ristmikud.tallinn.ee/last/<cam>.jpg`
(e.g. `cam103.jpg`, 1280×720).

## Important: there is no photo archive

The website only ever exposes the **latest** frame for each camera — it is
overwritten continuously and no history is kept. There is therefore no
back-catalogue of "all photos" to download. The only way to build a collection
is to **capture frames going forward on a schedule**. This script does one
capture per run; you schedule it (cron) to accumulate images over time.

## Prerequisites

- `bash`, `curl`, and `file` (standard on Linux/macOS)
- `rclone` — install from <https://rclone.org/install/>
- For upscaling only: `python3` + Pillow (`pip install Pillow`)

## One-time setup

1. Configure an rclone remote for your Google Drive:

   ```bash
   rclone config
   ```

   - `n` for new remote, name it `gdrive`
   - storage type: `drive` (Google Drive)
   - follow the OAuth browser prompt to authorize your account
   - accept defaults for the rest

2. Verify it works:

   ```bash
   rclone lsd gdrive:
   ```

The script uploads to the remote/folder named in `REMOTE` (default
`gdrive:cam103`). rclone creates the folder automatically if it doesn't exist.

## Usage

```bash
chmod +x capture.sh        # first time only
./capture.sh               # capture cam103 -> gdrive:cam103/
```

Each run creates a timestamped file in the Drive folder, e.g.
`cam103_20260602_114735.jpg` (UTC timestamp).

### Configuration (environment variables)

| Variable     | Default                              | Description                              |
|--------------|--------------------------------------|------------------------------------------|
| `CAM`        | `cam103`                             | Camera id to capture                     |
| `BASE_URL`   | `https://ristmikud.tallinn.ee/last`  | Base path for camera frames              |
| `REMOTE`     | `gdrive:<CAM>`                       | rclone destination remote:folder         |
| `WORKDIR`    | a temp dir                           | Local scratch directory                  |
| `KEEP_LOCAL` | `0`                                  | Set to `1` to keep the local JPEG too    |
| `FACTOR`     | `1`                                  | Upscale factor (`1` = off, e.g. `2`, `3`)|
| `QUALITY`    | `95`                                 | JPEG quality used when upscaling         |

Examples:

```bash
CAM=cam104 ./capture.sh
REMOTE="gdrive:traffic-cams/cam103" ./capture.sh
KEEP_LOCAL=1 WORKDIR=./shots ./capture.sh
```

## Scheduling (build up "all photos" over time)

Run it from cron. Example — capture cam103 every minute:

```cron
* * * * * /full/path/to/cam103-extractor/capture.sh >> /var/log/cam103.log 2>&1
```

Every 5 minutes:

```cron
*/5 * * * * /full/path/to/cam103-extractor/capture.sh >> /var/log/cam103.log 2>&1
```

> Note: the source frame typically updates roughly once a minute, so capturing
> more often than that just stores duplicates.

## Upscaling

`upscale.py` enlarges a frame using high-quality Lanczos resampling plus a mild
unsharp mask. Enable it in the capture by setting `FACTOR`:

```bash
FACTOR=2 ./capture.sh          # upload a 2x (2560x1440) version instead
```

Or run it standalone:

```bash
python3 upscale.py in.jpg out.jpg --factor 2 --quality 95
```

> Reality check: upscaling makes the image larger and visually smoother, but it
> **cannot add real detail** the camera never recorded. For genuine
> super-resolution you'd need an ML model such as Real-ESRGAN.

## Timed capture test

`test_capture.sh` captures repeatedly over a window and upscales each frame.
The defaults reproduce the "1 minute, every 10 seconds, upscaled 2x" test:

```bash
./test_capture.sh                          # 6 grabs, 10s apart, 2x upscale (local only)
DURATION=120 INTERVAL=15 FACTOR=3 ./test_capture.sh

# also upload each upscaled frame to Drive via rclone:
UPLOAD=1 REMOTE=gdrive:cam103 ./test_capture.sh
```

Byte-identical consecutive frames are detected and skipped, so you keep only
genuinely new images. (In practice cam103 refreshes about every 10–12 seconds,
so a 10s interval yields mostly fresh frames.)

## Counting persons, cars and buses

`detect.py` runs a COCO-pretrained [Ultralytics YOLO](https://docs.ultralytics.com/)
detector and counts `person`, `car` and `bus` (also reporting `truck` /
`motorcycle`, which are easily confused with cars/buses).

```bash
pip install ultralytics            # first time (downloads ~6 MB weights on first run)

python3 detect.py frame.jpg                 # analyze a local image
python3 detect.py --cam cam103              # grab + analyze the live cam103 frame
python3 detect.py --cam cam103 --save out.jpg   # also write an annotated image
```

Options: `--model` (e.g. `yolov8s.pt` for higher accuracy), `--conf`
(confidence threshold, default 0.25). Detection is best-effort — small or
distant objects on a 1280×720 traffic frame may be missed; use a larger model
or higher-resolution source for better recall.

## How it works

1. `curl` downloads `<BASE_URL>/<CAM>.jpg` (with retries on transient errors).
2. Validates the result is a non-empty JPEG.
3. `rclone copyto` uploads it to `<REMOTE>/<CAM>_<UTC timestamp>.jpg`.
4. Cleans up the local copy unless `KEEP_LOCAL=1`.
