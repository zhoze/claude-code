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

## How it works

1. `curl` downloads `<BASE_URL>/<CAM>.jpg` (with retries on transient errors).
2. Validates the result is a non-empty JPEG.
3. `rclone copyto` uploads it to `<REMOTE>/<CAM>_<UTC timestamp>.jpg`.
4. Cleans up the local copy unless `KEEP_LOCAL=1`.
