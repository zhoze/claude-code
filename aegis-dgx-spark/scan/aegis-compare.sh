#!/usr/bin/env bash
# AEGIS stage 2 — compare the machine (scan-report/facts.json) against the
# assumptions this repository's install code makes. Read-only apart from its
# own reports; --write-overrides additionally writes config/site-overrides.env.
#
#   ./scan/aegis-compare.sh
#   ./scan/aegis-compare.sh --write-overrides
#
# install.sh refuses to run until this passes with zero FAILs against the
# current scan. Amend code or machine, re-scan, re-compare.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}"
exec python3 "$HERE/aegis_compare.py" "$@"
