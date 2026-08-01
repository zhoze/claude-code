#!/usr/bin/env bash
# Prepare a fresh NVIDIA DGX Spark for GPU PDF password recovery.
#
# The DGX Spark ships with the CUDA driver + toolkit for its GB10 Grace
# Blackwell superchip already installed, so this script only adds hashcat,
# John the Ripper (for its reference pdf2john, used as a fallback), and the
# Python dependencies. Safe to re-run.
set -euo pipefail

echo "==> checking GPU / CUDA backend"
if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi -L || true
else
    echo "!! nvidia-smi not found. On a real DGX Spark the driver is preinstalled;" \
         "make sure you are running on the box itself, not a login shell."
fi

echo "==> installing hashcat + john + pip"
if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y
    sudo apt-get install -y hashcat john python3-pip
else
    echo "!! apt-get not available; install 'hashcat' and 'john' with your package manager."
fi

echo "==> installing Python dependencies"
pip3 install --user -r "$(dirname "$0")/requirements.txt"

echo "==> verifying hashcat sees the CUDA device"
# -I lists backend devices; grep for CUDA so a broken OpenCL-only setup is caught.
if command -v hashcat >/dev/null 2>&1; then
    hashcat -I 2>/dev/null | grep -A2 -i "Backend Device" || true
    echo
    echo "If no CUDA device is listed above, your hashcat build lacks the CUDA"
    echo "backend. Rebuild from source against the DGX CUDA toolkit:"
    echo "    git clone https://github.com/hashcat/hashcat && cd hashcat && make -j"
fi

echo "==> optional: fetch the rockyou wordlist for dictionary attacks"
WL=/usr/share/wordlists/rockyou.txt
if [ ! -f "$WL" ] && [ -f "$WL.gz" ]; then
    sudo gunzip -k "$WL.gz" && echo "unpacked $WL"
fi

echo "==> done. Try:  python3 crack_pdf.py yourfile.pdf --dry-run"
