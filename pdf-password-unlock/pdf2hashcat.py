#!/usr/bin/env python3
"""Extract a hashcat/John-compatible password hash from an encrypted PDF.

The encryption metadata (the /Encrypt dictionary and document /ID) is *not*
itself protected by the password, so it can always be read from a locked PDF.
hashcat then attacks that metadata on the GPU without ever needing the file
contents.

Output format (identical to John the Ripper's ``pdf2john``)::

    $pdf$V*R*keylen*P*encMetadata*idLen*idHex*uLen*uHex*oLen*oHex

hashcat hash-mode is chosen automatically from the (V, R) pair by
``crack_pdf.py``; see ``mode_for()`` below for the mapping.
"""
from __future__ import annotations

import sys
from typing import Tuple

try:
    from pypdf import PdfReader
    from pypdf.generic import ByteStringObject, TextStringObject
except ImportError:  # pragma: no cover - dependency hint
    sys.stderr.write(
        "pypdf is required: pip install pypdf  (see requirements.txt)\n"
    )
    raise


# (V, R) -> hashcat -m mode.  Ambiguous R keys fall back on V.
def mode_for(v: int, r: int) -> int:
    """Return the hashcat hash-mode for a PDF security handler (V, R)."""
    if r == 2:
        return 10400  # PDF 1.1-1.3 (Acrobat 2-4), RC4 40-bit
    if r == 3:
        return 10500  # PDF 1.4-1.6 (Acrobat 5-8), RC4/AES 128-bit
    if r == 4:
        return 10500  # PDF 1.5-1.6 (Acrobat 7-8), AES-128
    if r == 5:
        return 10600  # PDF 1.7 Level 3 (Acrobat 9), AES-256 (deprecated draft)
    if r == 6:
        return 10700  # PDF 1.7 Level 8 (Acrobat 10-11), AES-256
    raise ValueError(f"unsupported PDF security revision R={r} (V={v})")


def _raw_bytes(obj) -> bytes:
    """Coerce a pypdf string object into the raw bytes stored in the file."""
    if obj is None:
        return b""
    if isinstance(obj, ByteStringObject):
        return bytes(obj)
    if isinstance(obj, TextStringObject):
        # A /O or /U that decoded as text: recover the original bytes.
        return obj.get_original_bytes() if hasattr(obj, "get_original_bytes") else obj.encode("latin-1")
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj)
    return str(obj).encode("latin-1")


def _signed_int32(p: int) -> int:
    """PDF /P is a signed 32-bit integer; normalise pypdf's value."""
    p &= 0xFFFFFFFF
    return p - 0x100000000 if p & 0x80000000 else p


def extract(path: str) -> Tuple[str, int]:
    """Return ``(hash_string, hashcat_mode)`` for the encrypted PDF at *path*.

    Raises ``ValueError`` if the PDF is not encrypted or uses an
    unsupported handler.
    """
    reader = PdfReader(path)
    trailer = reader.trailer

    enc_ref = trailer.get("/Encrypt")
    if enc_ref is None:
        raise ValueError(f"{path}: PDF is not encrypted (no /Encrypt dictionary)")
    enc = enc_ref.get_object()

    filt = enc.get("/Filter")
    if filt is not None and str(filt) != "/Standard":
        raise ValueError(
            f"{path}: unsupported security handler {filt!s} "
            "(only the Standard handler can be attacked with hashcat)"
        )

    v = int(enc.get("/V", 0))
    r = int(enc.get("/R", 0))
    length = int(enc.get("/Length", 40))
    if r >= 5:
        length = 256  # AES-256 handlers always use a 256-bit key
    p = _signed_int32(int(enc.get("/P", 0)))

    o = _raw_bytes(enc.get("/O"))
    u = _raw_bytes(enc.get("/U"))
    if not o or not u:
        raise ValueError(f"{path}: /O or /U entry missing from /Encrypt")

    # /EncryptMetadata defaults to true when absent.
    encrypt_metadata = 1 if bool(enc.get("/EncryptMetadata", True)) else 0

    # Document ID lives in the trailer, not the /Encrypt dict.
    id_bytes = b""
    id_arr = trailer.get("/ID")
    if id_arr:
        try:
            id_bytes = _raw_bytes(id_arr[0])
        except Exception:
            id_bytes = b""

    mode = mode_for(v, r)

    parts = [
        "$pdf$" + str(v),
        str(r),
        str(length),
        str(p),
        str(encrypt_metadata),
        str(len(id_bytes)),
        id_bytes.hex(),
        str(len(u)),
        u.hex(),
        str(len(o)),
        o.hex(),
    ]
    return "*".join(parts), mode


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="Extract a hashcat-compatible hash from an encrypted PDF."
    )
    ap.add_argument("pdf", help="path to the password-protected PDF")
    ap.add_argument(
        "-o", "--out", help="write the hash to this file instead of stdout"
    )
    ap.add_argument(
        "--mode",
        action="store_true",
        help="also print the detected hashcat -m mode to stderr",
    )
    args = ap.parse_args(argv)

    try:
        hash_str, mode = extract(args.pdf)
    except Exception as exc:  # noqa: BLE001 - user-facing CLI
        sys.stderr.write(f"error: {exc}\n")
        return 1

    if args.out:
        with open(args.out, "w") as fh:
            fh.write(hash_str + "\n")
        sys.stderr.write(f"wrote hash to {args.out}\n")
    else:
        print(hash_str)

    if args.mode:
        sys.stderr.write(f"hashcat mode: -m {mode}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
