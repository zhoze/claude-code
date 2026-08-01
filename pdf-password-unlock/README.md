# GPU PDF Password Recovery (DGX Spark)

Recover the password for **a PDF you own** and write out an unlocked copy,
using the full GPU throughput of an NVIDIA DGX Spark (GB10 Grace Blackwell).

This is a password *recovery* tool for your own documents — the same job a
"Forgot my password?" flow does, just orders of magnitude faster because the
work runs on the GPU via [hashcat](https://hashcat.net). It does not break,
bypass, or exploit anything: it reads the unencrypted `/Encrypt` metadata that
every PDF stores in the clear, hands the resulting hash to hashcat, and tries
password candidates until one validates. Only use it on files you are
authorized to open.

## How it works

```
locked.pdf ──pdf2hashcat.py──▶ $pdf$… hash ──hashcat (GPU)──▶ password ──pikepdf──▶ unlocked.pdf
```

1. **`pdf2hashcat.py`** reads the `/Encrypt` dictionary and document `/ID` from
   the locked PDF (no password needed) and emits a hash in the exact format
   hashcat expects. The correct hash-mode is detected automatically:

   | PDF encryption | Handler (V,R) | hashcat `-m` |
   |---|---|---|
   | RC4 40-bit (Acrobat 2–4) | 1,2 | 10400 |
   | RC4/AES 128-bit (Acrobat 5–8) | 2/4, 3 | 10500 |
   | AES-128 (Acrobat 7–8) | 4,4 | 10500 |
   | AES-256 draft (Acrobat 9) | 5,5 | 10600 |
   | **AES-256 (Acrobat X/XI, PDF 2.0)** | 5,6 | 10700 |

2. **`crack_pdf.py`** launches hashcat on the DGX Spark's GPU with an
   escalating chain of attacks and, on success, decrypts the file.

3. **`pikepdf`** writes a new, password-free copy and verifies it opens.

## Quick start

```bash
# One-time, on the DGX Spark:
./setup_dgx.sh

# Fully automatic (wordlist → wordlist+rules → brute force):
python3 crack_pdf.py locked.pdf

# See the plan and the exact hashcat commands without running them:
python3 crack_pdf.py locked.pdf --dry-run
```

The recovered copy is written next to the original as
`locked.decrypted.pdf` (override with `-o`).

## Driving the GPU hard

`crack_pdf.py` pins every hashcat run to the CUDA backend with an aggressive
profile so the DGX Spark's GPU runs at full tilt:

```
-O   optimized kernels (fastest; caps candidate length)
-w 4 "nightmare" workload — maximum GPU utilisation
-d 1 the GB10 GPU device
--backend-ignore-opencl   CUDA only, never the slower OpenCL path
```

Tune the search:

```bash
# Your own wordlist + rules
python3 crack_pdf.py locked.pdf --wordlist rockyou.txt --rules best64.rule

# Pure brute force: every 1–8 char lower-case + digit password
python3 crack_pdf.py locked.pdf --mask '?l?d' --min-len 1 --max-len 8

# Squeeze more out of the box (multi-GPU, temp limits, etc.)
python3 crack_pdf.py locked.pdf --extra '-w 4 --hwmon-temp-abort=90'
```

hashcat mask charsets: `?l` a–z · `?u` A–Z · `?d` 0–9 · `?s` symbols ·
`?a` all of them. Combine them, e.g. `--mask '?u?l?l?l?d?d'`.

## A note on speed vs. encryption type

The older handlers (modes 10400/10500) are extremely fast — billions of
guesses/sec — so short passwords fall quickly. Modern **AES-256 (mode 10700)**
deliberately uses a hardened, iterated hash, so guess rates are far lower.
That difficulty is exactly why you want the DGX Spark's GPU: it is the
difference between "infeasible on a laptop" and "tractable." Even so, a long,
random AES-256 password may be out of reach — recovery works best when the
password is human-chosen. Start with a wordlist + rules before brute force.

## Run without hashcat

`pdf2hashcat.py` is standalone — use it to feed any cracker (John the Ripper,
a cloud GPU cluster, etc.):

```bash
python3 pdf2hashcat.py locked.pdf -o hash.txt --mode
hashcat -m 10700 hash.txt -a 0 rockyou.txt -O -w 4
```

## Files

| File | Purpose |
|---|---|
| `crack_pdf.py` | Orchestrator: extract → GPU crack → decrypt |
| `pdf2hashcat.py` | Standalone PDF → hashcat-hash extractor |
| `setup_dgx.sh` | Install hashcat/john/deps on a DGX Spark |
| `requirements.txt` | Python dependencies |

## Legitimate use only

Use this exclusively on PDFs you own or are explicitly authorized to unlock.
Recovering passwords for files you do not have permission to access may be
illegal in your jurisdiction.
