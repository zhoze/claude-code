#!/usr/bin/env python3
"""Convert PDF files to editable Word (.docx) documents.

Usage:
    python pdf_to_docx.py input.pdf                  # writes input.docx
    python pdf_to_docx.py input.pdf -o out.docx
    python pdf_to_docx.py input.pdf --pages 1-3,5    # only selected pages (1-based)
    python pdf_to_docx.py a.pdf b.pdf -d converted/  # batch into a folder
"""

import argparse
import sys
from pathlib import Path

from pdf2docx import Converter


def parse_pages(spec: str) -> list[int]:
    """Turn '1-3,5' into zero-based page indices [0, 1, 2, 4]."""
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = (int(x) for x in part.split("-", 1))
            if start < 1 or end < start:
                raise ValueError(f"invalid page range: {part}")
            pages.extend(range(start - 1, end))
        else:
            n = int(part)
            if n < 1:
                raise ValueError(f"invalid page number: {part}")
            pages.append(n - 1)
    return pages


def convert(pdf_path: Path, docx_path: Path, pages: list[int] | None = None,
            password: str | None = None) -> Path:
    """Convert a single PDF to DOCX and return the output path."""
    if not pdf_path.is_file():
        raise FileNotFoundError(f"file not found: {pdf_path}")
    docx_path.parent.mkdir(parents=True, exist_ok=True)

    cv = Converter(str(pdf_path), password=password)
    try:
        cv.convert(str(docx_path), pages=pages)
    finally:
        cv.close()
    return docx_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Convert PDF files to DOCX.")
    ap.add_argument("pdfs", nargs="+", type=Path, help="input PDF file(s)")
    out = ap.add_mutually_exclusive_group()
    out.add_argument("-o", "--output", type=Path,
                     help="output .docx path (single input only)")
    out.add_argument("-d", "--out-dir", type=Path,
                     help="directory for converted files")
    ap.add_argument("--pages", help="pages to convert, e.g. '1-3,5' (1-based)")
    ap.add_argument("--password", help="password for encrypted PDFs")
    args = ap.parse_args(argv)

    if args.output and len(args.pdfs) > 1:
        ap.error("--output works with a single input; use --out-dir for several")

    try:
        pages = parse_pages(args.pages) if args.pages else None
    except ValueError as e:
        ap.error(str(e))

    failures = 0
    for pdf in args.pdfs:
        if args.output:
            target = args.output
        elif args.out_dir:
            target = args.out_dir / pdf.with_suffix(".docx").name
        else:
            target = pdf.with_suffix(".docx")
        try:
            convert(pdf, target, pages=pages, password=args.password)
            print(f"OK   {pdf} -> {target}")
        except Exception as e:  # keep going with remaining files
            failures += 1
            print(f"FAIL {pdf}: {e}", file=sys.stderr)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
