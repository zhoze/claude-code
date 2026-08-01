#!/usr/bin/env bash
# Everything that can be checked without root and without the box.
# Run before every commit and after every refactor.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
rc=0

echo "== shell syntax =="
# One bash -n per file: extra arguments to bash -n are positional params of
# the FIRST script, not additional files — the old single call checked one
# script and silently blessed the rest.
sh_fail=0
for f in install.sh bootstrap/*.sh scan/*.sh tests/*.sh scripts/*.sh; do
  [[ -f "$f" ]] || continue
  bash -n "$f" || { echo "  syntax error: $f"; sh_fail=1; }
done
[[ $sh_fail -eq 0 ]] && echo "  ok" || rc=1

echo "== python syntax =="
python3 -m py_compile src/egress_gate/*.py src/aegis_core/*.py scripts/*.py scan/*.py \
  && echo "  ok" || rc=1

echo "== config parses =="
python3 - <<'PY' || rc=1
import tomllib, glob
for f in ["config/egress.toml", "config/routing.toml", "config/models.toml",
          *glob.glob("config/agents/*.toml")]:
    tomllib.load(open(f, "rb")); print(f"  ok {f}")
PY

echo "== nftables template =="
# The template needs rendering (@@ADMIN_USER@@) and referenced users to
# exist before `nft -c` fully passes; off-box we check what we can and say
# exactly what was skipped instead of failing on a box that is not the Spark.
if grep -q '@@ADMIN_USER@@' config/nftables/aegis.nft.tmpl; then
  echo "  ok template contains the admin-user token"
else
  echo "  FAIL template lost its @@ADMIN_USER@@ token"; rc=1
fi
if command -v nft >/dev/null; then
  rendered=$(mktemp)
  sed "s/@@ADMIN_USER@@/$(id -un)/g" config/nftables/aegis.nft.tmpl > "$rendered"
  if id aegis-proxy >/dev/null 2>&1; then
    nft -c -f "$rendered" && echo "  ok full syntax check" || rc=1
  else
    # Users absent (not the Spark): a parse failure OTHER than unknown-user
    # is still a real defect worth failing on.
    errs=$(nft -c -f "$rendered" 2>&1 | grep -v 'User does not exist' \
           | grep -c 'Error') || true
    if [[ "${errs:-0}" -eq 0 ]]; then
      echo "  ok grammar (service users absent on this box; full check runs on the Spark)"
    else
      nft -c -f "$rendered" 2>&1 | grep -v 'User does not exist' | head -5
      echo "  FAIL template has non-user errors"; rc=1
    fi
  fi
  rm -f "$rendered"
else
  echo "  skipped nft check (nft not installed)"
fi

echo "== gate unit tests =="
python3 tests/test_gate.py || rc=1

echo "== registry unit tests =="
python3 tests/test_registry.py || rc=1

echo "== ingest unit tests =="
python3 tests/test_ingest.py || rc=1

echo
[[ $rc -eq 0 ]] && echo "ALL CHECKS PASSED" || echo "FAILURES — do not deploy"
echo "Note: tests/verify-egress.sh is separate. It needs root and a running"
echo "gate, and it is the one that proves the boundary actually holds."
exit $rc
