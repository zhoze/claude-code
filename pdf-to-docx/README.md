# pdf-to-docx

Small command-line tool that converts PDF files into editable Word (`.docx`)
documents. It uses [pdf2docx](https://github.com/ArtifexSoftware/pdf2docx),
which keeps text, paragraphs, tables, images and basic page layout.

## Install

```bash
pip install -r requirements.txt
```

## Usage

```bash
# input.pdf -> input.docx (next to the PDF)
python pdf_to_docx.py input.pdf

# choose the output file
python pdf_to_docx.py input.pdf -o result.docx

# convert only some pages (1-based)
python pdf_to_docx.py input.pdf --pages 1-3,5

# convert many files into a folder
python pdf_to_docx.py *.pdf -d converted/

# password-protected PDF
python pdf_to_docx.py secret.pdf --password hunter2
```

Use it from Python too:

```python
from pathlib import Path
from pdf_to_docx import convert

convert(Path("input.pdf"), Path("output.docx"))
```

## Notes

- Works best on PDFs that contain real text. Scanned PDFs (images of pages)
  convert to a Word file holding those images; run OCR first (e.g.
  `ocrmypdf scanned.pdf searchable.pdf`) if you need editable text.
- Exit code is `1` if any file failed, so it can be used in scripts.
