#!/usr/bin/env python3
"""GPU-accelerated PDF password recovery, tuned for the NVIDIA DGX Spark.

Pipeline
--------
1. Read the /Encrypt metadata from your locked PDF and emit a hashcat hash
   (``pdf2hashcat.py``).  No password is needed for this step.
2. Launch hashcat on the DGX Spark's GB10 Blackwell GPU with an escalating
   set of attacks (wordlist -> wordlist+rules -> mask brute force), each
   pinned to the CUDA backend with an aggressive workload profile so the GPU
   runs flat out.
3. When hashcat recovers the password, decrypt the PDF into a new,
   password-free copy (``pikepdf``) and verify it opens.

This only works on PDFs *you own* -- it is a password *recovery* tool for
your own documents, the same job "Forgot my password" flows do, just far
faster.

Usage
-----
    # Fully automatic: sensible escalating attacks
    python3 crack_pdf.py locked.pdf

    # Point at your own wordlist and rules
    python3 crack_pdf.py locked.pdf --wordlist rockyou.txt --rules best64.rule

    # Pure brute force: all 1-8 char lower+digit passwords
    python3 crack_pdf.py locked.pdf --mask '?l?d' --min-len 1 --max-len 8

    # Just print the hash and the command it would run, then stop
    python3 crack_pdf.py locked.pdf --dry-run

Requires: hashcat >= 6.2 with a working CUDA backend, and python `pikepdf`
+ `pypdf`.  Run ``setup_dgx.sh`` first on a fresh DGX Spark.
"""
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pdf2hashcat


# ---------------------------------------------------------------------------
# DGX Spark GPU tuning
# ---------------------------------------------------------------------------
# -O   optimized kernels: much faster, caps candidate length (fine for the
#      typical human password lengths these attacks target).
# -w 4 "nightmare" workload profile: maximum GPU utilisation. Use -w 3 if the
#      box must stay interactive.
# -d 1 first CUDA device. The DGX Spark exposes its GB10 GPU as device 1.
# --backend-ignore-opencl keeps hashcat on CUDA only, avoiding the slower
#      OpenCL path some driver stacks also advertise.
DGX_GPU_FLAGS = [
    "-O",
    "-w", "4",
    "-d", "1",
    "--backend-ignore-opencl",
]


def resolve_hashcat(preferred: str | None) -> str:
    """Return a usable hashcat binary, honouring an explicit --hashcat path."""
    if preferred:
        # An explicit path or a name resolvable on PATH.
        if os.path.isfile(preferred) and os.access(preferred, os.X_OK):
            return preferred
        found = shutil.which(preferred)
        if found:
            return found
        sys.exit(f"hashcat binary not found or not executable: {preferred}")
    hc = shutil.which("hashcat")
    if not hc:
        sys.exit(
            "hashcat not found on PATH. Install it first (see setup_dgx.sh) "
            "or pass --dry-run to just generate the hash + command."
        )
    return hc


def build_attacks(args, hash_file: str) -> list[list[str]]:
    """Return an ordered list of hashcat argv lists to try in sequence."""
    base = [args.hashcat, "-m", str(args.mode), hash_file, *DGX_GPU_FLAGS]
    if args.extra:
        base += shlex.split(args.extra)

    attacks: list[list[str]] = []

    # If the user gave an explicit mask, honour only that.
    if args.mask:
        for length in range(args.min_len, args.max_len + 1):
            attacks.append(base + ["-a", "3", args.mask * length])
        return attacks

    # 1) Straight wordlist.
    if args.wordlist:
        attacks.append(base + ["-a", "0", args.wordlist])
        # 2) Wordlist + rules (huge coverage boost for human passwords).
        rules = args.rules or _default_rule()
        if rules:
            attacks.append(base + ["-a", "0", args.wordlist, "-r", rules])

    # 3) Brute force by increasing length over a broad charset.
    #    ?1 = lower+upper+digit; keep it to a sane max unless overridden.
    charset = ["-1", "?l?u?d"]
    for length in range(args.min_len, args.max_len + 1):
        attacks.append(base + charset + ["-a", "3", "?1" * length])

    return attacks


def _default_rule() -> str | None:
    for cand in (
        "/usr/share/hashcat/rules/best64.rule",
        "/usr/local/share/hashcat/rules/best64.rule",
    ):
        if os.path.exists(cand):
            return cand
    return None


def run_attack(argv: list[str]) -> bool:
    """Run one hashcat attack. Return True if it exhausted/cracked cleanly."""
    print("\n$ " + " ".join(shlex.quote(a) for a in argv), flush=True)
    # hashcat exit codes: 0 = cracked, 1 = exhausted, >1 = error.
    proc = subprocess.run(argv)
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    print(f"hashcat exited with status {proc.returncode}", file=sys.stderr)
    return False


def recover_password(hashcat: str, mode: int, hash_file: str) -> str | None:
    """Ask hashcat's potfile whether this hash is already cracked."""
    out = subprocess.run(
        [hashcat, "-m", str(mode), hash_file, "--show", "--outfile-format", "2"],
        capture_output=True, text=True,
    )
    pw = out.stdout.strip()
    # --outfile-format 2 prints just the plaintext (may be empty if uncracked).
    return pw.splitlines()[0] if pw else None


def decrypt_pdf(src: str, password: str, dest: str) -> None:
    """Write a password-free copy of *src* using the recovered *password*."""
    import pikepdf

    with pikepdf.open(src, password=password) as pdf:
        pdf.save(dest)  # saving without an Encryption= removes protection
    # Sanity check: the output really is openable with no password.
    with pikepdf.open(dest):
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("pdf", help="the password-protected PDF you own")
    ap.add_argument("-o", "--output", help="path for the decrypted copy "
                    "(default: <name>.decrypted.pdf)")
    ap.add_argument("--wordlist", help="wordlist file for dictionary attack")
    ap.add_argument("--rules", help="hashcat rules file (default: best64 if present)")
    ap.add_argument("--mask", help="hashcat charset unit repeated per length, "
                    "e.g. '?l?d'. Overrides the default attack chain.")
    ap.add_argument("--min-len", type=int, default=1, help="min length for mask/brute (default 1)")
    ap.add_argument("--max-len", type=int, default=6, help="max length for mask/brute (default 6)")
    ap.add_argument("--extra", help="extra raw args appended to every hashcat call")
    ap.add_argument("--hashcat", help="path to the hashcat binary")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the hash and planned commands, then exit")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.pdf):
        sys.exit(f"no such file: {args.pdf}")

    # 1) Extract the hash.
    try:
        hash_str, mode = pdf2hashcat.extract(args.pdf)
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"could not read encryption metadata: {exc}")
    args.mode = mode
    print(f"[+] detected hashcat mode -m {mode}")
    print(f"[+] hash: {hash_str[:70]}{'...' if len(hash_str) > 70 else ''}")

    hash_fd, hash_file = tempfile.mkstemp(prefix="pdfhash_", suffix=".txt")
    with os.fdopen(hash_fd, "w") as fh:
        fh.write(hash_str + "\n")

    if args.dry_run:
        args.hashcat = args.hashcat or (shutil.which("hashcat") or "hashcat")
        attacks = build_attacks(args, hash_file)
        print("\n[dry-run] hash written to:", hash_file)
        print("[dry-run] would run, in order:")
        for a in attacks:
            print("  " + " ".join(shlex.quote(x) for x in a))
        return 0

    args.hashcat = resolve_hashcat(args.hashcat)
    attacks = build_attacks(args, hash_file)

    # 2) Run attacks until the password is recovered.
    password = recover_password(args.hashcat, mode, hash_file)  # maybe already cracked
    if not password:
        for attack in attacks:
            run_attack(attack)
            password = recover_password(args.hashcat, mode, hash_file)
            if password:
                break

    if not password:
        os.unlink(hash_file)
        print("\n[-] password not recovered with the attacks tried. "
              "Widen --max-len, supply a bigger --wordlist, or add --rules.")
        return 2

    print(f"\n[+] PASSWORD RECOVERED: {password!r}")

    # 3) Decrypt.
    dest = args.output or str(Path(args.pdf).with_suffix("")) + ".decrypted.pdf"
    try:
        decrypt_pdf(args.pdf, password, dest)
        print(f"[+] wrote password-free copy: {dest}")
    except Exception as exc:  # noqa: BLE001
        print(f"[-] recovered the password but could not decrypt: {exc}",
              file=sys.stderr)
        os.unlink(hash_file)
        return 3

    os.unlink(hash_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
