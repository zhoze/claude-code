# AMENDMENTS

Every change from the original `aegisdgxspark` tree, with the defect it
fixes. The review that produced this list examined the bootstrap scripts,
the Python source, the configs, every systemd unit, the tests, and the docs
— against a DGX Spark profile (DGX OS 7, Ubuntu 24.04 aarch64, GB10
Grace-Blackwell, 128 GiB unified memory, CUDA 13).

The headline finding: **the original tree could not complete installation
on a clean DGX Spark**, and its most dangerous failure was ordering — the
default-deny firewall applied one step *before* the step that could not
succeed, leaving a headless box with broken DNS and a gate that refused to
start.

Bootstrap renumbering (the old numbers were not the run order):

| old | new | phase |
|-----|-----|-------|
| 00-preflight | 00-preflight | checks + D1 |
| 01-users-and-dirs | 01-users-and-dirs | identities |
| 02-packages | 02-packages | packages |
| 03-python-env | 03-python-env | code + venv |
| — | **04-credentials** (new) | systemd-creds |
| 04-egress-gate | **05-egress-gate** | the boundary |
| 06-models | **06-models** | runtime (now llama.cpp) |
| 07-knowledge | **07-knowledge** | vector db + ingestion |
| 05-storage-and-backup | **08-storage-and-backup** | rotation, timers, backup |
| — | **09-scaffold** (new) | core/agent units, zone-sync |

---

## 0. New: the scan → compare → amend gate

`install.sh` refuses to run until `scan/aegis-scan.sh` (a strictly
read-only profile of the machine: GPU device nodes, uids, firewall state,
Docker, apt sources, listeners, prior AEGIS state) and
`scan/aegis-compare.sh` (every install-code assumption checked against
those facts, each finding naming the file that holds the assumption) pass
with zero FAILs against the *current* facts (hash-matched).
`--write-overrides` derives `config/site-overrides.env` (admin user,
memory-based caps, GPU node style) that every phase sources.
`scan/REVIEW-WITH-CLAUDE.md` is the guided second-pair-of-eyes procedure
for running Claude Code on the box against the scan report.

Why: fixed checklists miss what a specific machine does differently; the
original tree's worst defects were exactly assumptions (uid names, CDN
hosts, device nodes) that a scan of the real box exposes immediately.

## 1. Approval flow (was broken end-to-end)

* `approve.py` crashed for any non-root operator: `os.chown()` to the
  aegis-proxy group requires membership in that group, which operators
  never had. **Removed the chown entirely** — the queue directory is setgid
  `aegis-operators`, so tokens land with the right group with no chown.
* The Gate could not burn tokens: the queue was mode **3770 (sticky)**, and
  sticky directories only let the file owner unlink. The resulting
  `PermissionError` was swallowed by a bare `except OSError`, so every
  gated request was denied silently *with a valid token sitting in the
  queue*. **Queue is now 2770 root:aegis-operators (no sticky, no ACLs)**;
  `gate.py` treats an unburnable token as no token (fail closed) but logs
  and audits it loudly (`token_unlink_denied`), and `aegis-proxy` holds
  aegis-operators as a supplementary group (bootstrap/01 + the unit).
  Cost accepted: operators can delete each other's tokens — this is a
  single-operator deployment; sticky was what broke burning.
* `aegis-approve --list` / `--revoke-all` were unreachable (argparse
  demanded a host and `--reason` first). Host is now optional and the
  mint-path arguments are validated after parsing.
* `--revoke-all` reports and exits non-zero on tokens it could not delete
  (it used to swallow errors, which let verify-egress test 4 run against a
  stale token).
* Identity in the token (`approved_by`) now comes from `geteuid()`, not
  `getpass.getuser()` ($USER survives sudo/su and misattributes the trail).
* The Gate no longer `mkdir`s the approvals directory at startup (it would
  own what it must only read), and denies peers it cannot identify even on
  routes with an empty `allow_uids` (the docstring claimed this; the code
  did not do it).
* setfacl use removed everywhere (bootstrap/01 called it before phase 02
  installed `acl`, and its `chgrp aegis-proxy` fallback broke operator
  access to the queue).

## 2. Credentials (used to strand the box)

* `aegis-egress-gate.service` demanded two `LoadCredentialEncrypted=` files
  that no script and no doc created — the unit refused to start, one step
  *after* default-deny was applied. Now **`ImportCredential=`** (systemd
  255; absence tolerated) **plus a new phase 04** that prompts for the
  Anthropic/NVIDIA keys and encrypts them with `systemd-creds` *before*
  the boundary goes up. Skipping a key is recorded and legal — local-only
  operation needs neither.
* Honesty fix in gate.py's docstring: a CONNECT tunnel is opaque TLS — the
  Gate cannot inject API keys into it. The keys are *staged* under the uid
  Core cannot read, for the future release step. The old text claimed an
  impossible property.
* "One token, one request" corrected to "one token, one connection"
  (keep-alive requests ride a single tunnel until it closes or hits the
  byte cap). Documented rather than re-engineered — per-request accounting
  inside an opaque tunnel would require MITM.

## 3. Firewall (used to break the whole box)

All changes in `config/nftables/aegis.nft.tmpl` (replaces `aegis.nft`):

* **DNS died system-wide on apply**: only aegis-proxy and root could reach
  port 53, but Ubuntu's resolver daemon runs as `systemd-resolve`. Added
  its uid (53/tcp+udp, 853 for DoT). Same class of failure fixed for
  **`_apt`** (apt's sandbox uid — apt and unattended security upgrades
  silently died) and **`systemd-timesync`** (NTP; TLS depends on the
  clock).
* **`flush ruleset` destroyed Docker's and the NVIDIA container toolkit's
  tables** on a DGX OS box. The policy now owns `table inet aegis` only,
  replaced atomically (declare/delete/define); the forward chain keeps
  policy drop but accepts the container bridges (docker0, br-*).
* The admin account had no DNS and no loopback (so `git`, `curl`,
  `curl 127.0.0.1:8000` all failed for the operator). Root had no loopback
  either. Both fixed; ICMPv6 (NDP — kernel-generated, uid-less) and
  operator ICMP added to output.
* The hardcoded username `priit` (sed-patched in place, one-shot,
  reverted by any re-run of the old 03) is now a **`@@ADMIN_USER@@`
  template token**, rendered fresh on every phase-05 run from the
  persisted `/etc/default/aegis`.
* **Apply is now guarded by a dead-man switch**: previous ruleset
  snapshotted, `systemd-run` timer restores it in 3 minutes unless the
  operator types CONFIRM; DNS/apt/https probes run between apply and
  confirm. `/etc/nftables.conf` is backed up once to
  `/etc/nftables.conf.pre-aegis` before being overwritten.
* Ordering contradiction resolved: the old 04 header said "run LAST —
  lockdown before model downloads breaks them" while the sequence ran it
  4th of 7. The answer (downloads go *through* the gate on auto routes) is
  now stated in phase 06 and works, because the HF hosts are current (§6).

## 4. Vector DB (was fictional)

* `aegis-vectordb.service` invoked `python -m qdrant_client.local_server`
  — **no such module exists** (qdrant-client is a client). Phase 07 now
  installs the **official Qdrant aarch64 release binary** (version pinned
  or resolved-and-recorded, sha256 recorded in `/etc/default/aegis`),
  config `config/qdrant.yaml` (loopback, gRPC off, telemetry off).
* It ran as `aegis-ht` — the same uid as every high-trust agent, letting a
  compromised agent rewrite index files under the server and bypass
  query-time permission filters. Now a **dedicated `aegis-vectordb` uid**
  owns `/aegis/vectordb`; agents reach the db only via 127.0.0.1:6333.
  `documents.toml` no longer asks for filesystem write on the db.

## 5. Model runtime (vLLM → llama.cpp)

* `pip install vllm` has no usable wheels for GB10 (Blackwell sm_121,
  aarch64, CUDA 13) — the single likeliest hard failure of the original
  phase 3. Phase 06 now **builds llama.cpp** (release tag recorded; CUDA
  with CPU fallback) and serves an OpenAI-compatible API on
  127.0.0.1:8000, same surface the configs assumed.
* **New `aegis-embed.service`** serves the embedding model on :8001 — the
  port the firewall reserved and ingestion required, for which no service
  existed at all.
* `llm.env` schema replaced (CTX_SIZE/GPU_LAYERS/PARALLEL/EMBED_*); the
  placeholder-path crash-loop is gone — phase 06 writes real paths and the
  units refuse to start otherwise.
* Memory limits corrected for unified memory: GGUF weights are mmap'd
  page cache, so the old `MemoryHigh=72G` (≈ exactly the vLLM reservation)
  guaranteed reclaim pressure. Now a single `MemoryMax` backstop derived
  from measured RAM (site-overrides), no MemoryHigh.
* `DeviceAllow=/dev/nvidia*` removed: systemd does not glob device paths
  (the lines were no-ops), and GB10 exposes Tegra-style `/dev/nvgpu/*`
  nodes anyway. `SupplementaryGroups=video render` added; the scan records
  the actual node inventory.
* **`config/models.toml` now exists** (the docs demanded it; nothing
  shipped it), and a new `aegis-verify-model` ExecStartPre **verifies each
  model's sha256 against the registry on every service start**. Phase 06
  records repo/file/sha256/date per download and makes you cross-check the
  hash against the publisher's page.
* Model menu defaults sized for the Spark's 273 GB/s bandwidth: 32B-class
  Q5 as the interactive default, 70B Q4 as the explicit slow option,
  bge-m3 (multilingual — Estonian, D2) as the embedding default. All
  overridable; download URLs are plain HF `resolve/main` fetched **as
  aegis-llm through the gate**.

## 6. Egress allowlist

* `cdn-lfs.huggingface.co` alone no longer covers HF downloads — weight
  blobs redirect to Xet/CAS hosts. Added `cdn-lfs-us-1.hf.co`,
  `cdn-lfs-eu-1.hf.co`, `cas-bridge.xethub.hf.co`, `transfer.xethub.hf.co`
  (all `auto`, aegis-llm only), with a note explaining how to add the next
  CDN rotation from the audit log.
* Removed the `archive.ubuntu.com` / `security.ubuntu.com` routes: they
  could never work (apt speaks absolute-URI GET, not CONNECT; the entries
  defaulted to port 443 while apt uses 80) and arm64 uses ports.ubuntu.com
  anyway. Package traffic rides the `_apt`/root uid rules in the firewall,
  and the config now says so.

## 7. Agents and zones

* The high-trust drop-in was an example file **nothing installed**, so the
  phase-4 registry check always failed — and skipping it ran `documents`
  as aegis-lt *with Gate access*. Drop-ins are now **generated from the
  manifests** by `scripts/aegis-zone-sync.py` (phase 07/09), which also
  propagates `limits.memory_gib` into `MemoryMax` (the manifest limits
  used to be decorative).
* The generated high-trust drop-in **resets `ReadWritePaths=`** before
  granting its own: drop-ins append to path lists, so the old static file
  silently inherited write access to the low-trust scratch — a channel
  between the zones. Scratch is now split: `/aegis/scratch/lt` and
  `/aegis/scratch/ht`, each owned by its zone (the old single dir was
  owned by aegis-lt, so high-trust agents could not even chdir into their
  WorkingDirectory).
* `aegis-agent@.service` gained `Environment=PYTHONPATH=/aegis/src`
  (`python -m aegis_core.agent_runner` could never import; the in-file
  `sys.path` hack runs too late for `-m`).
* `agent_runner.preflight()` uses `pwd.getpwuid(os.geteuid())` instead of
  `getpass.getuser()` — an env-spoofable value has no business in the
  zone check that exists to catch misconfiguration.
* `registry.check_unit_matches()` now also flags the reverse drift (a
  low-trust manifest with a high-trust drop-in), and the zone path
  whitelist covers the split scratch dirs.
* `aegis_core` code is installed in phase 03 with everything else (it used
  to appear only at phase 4, while a phase-5 unit referenced it).

## 8. Core (was pointing at nothing)

* `aegis-core.service` ran `python -m aegis_core.service` — a module that
  did not exist; the unit crash-looped forever. A minimal, **honest stub**
  (`src/aegis_core/service.py`: /healthz on :8080, heartbeat log, clean
  shutdown, no fabricated decision log) now backs the unit, which phase 09
  installs and deliberately does **not** enable. `/aegis/run/core` and
  `/aegis/state` exist with aegis-core ownership (the unit's old
  `ReadWritePaths=/aegis/run` pointed at a root-owned dir it could not
  write).

## 9. Ingestion (completed)

* The embed→index step was a TODO. `ingest.py` now embeds via
  `127.0.0.1:8001/v1/embeddings` and upserts to Qdrant over plain REST
  (stdlib urllib — no qdrant-client/grpcio, nothing compiled), with
  deterministic uuid5 point ids (re-ingest is idempotent), collection
  auto-create, dimension-change detection, and classification/trust-zone
  payloads on every point (the part-13 "filter before ranking" contract).
* `chunk()` actually does the fixed-window fallback its docstring claimed
  (a paragraph longer than the window used to be emitted whole — straight
  past the embedding context) and no longer glues a stray `\n\n` onto
  chunk boundaries.
* `--reindex-all` skips `.reason` files (it used to quarantine the
  quarantine's own notes) — and an infrastructure failure (embed/db down)
  now stops the run with the documents left in place, instead of
  quarantining your files because a service was restarting.

## 10. Backup, reconcile, forget

* `aegis-backup.sh`: the `current` pointer was pre-created as a real
  directory, so the symlink could never exist — no hardlink dedup ever
  happened, `--verify` always failed, `--restore-test` restored nothing.
  Fixed + migration for the broken state. `--restore-test` honours its
  destination argument (off-by-one `$3`→`$2`). The manifest now hashes the
  whole snapshot, not just `config/*`. The prune never deletes the
  snapshot `current` points to. Off-site staging moved from world-readable
  `/tmp` (+ ineffective `shred` on flash) to root-only `/aegis/backups/tmp`.
  `rsync`, `age`, `openssh-client` are actually installed by phase 02.
  `/etc/default/aegis-backup` skeleton is created (it was referenced,
  never created). The backup unit gained sandboxing (it ran as root with
  none) and phase 08 runs + verifies the first snapshot immediately.
* `aegis-reconcile.py` no longer cries wolf on every run: it compares
  **gated** connections (not auto-class downloads) against Core's log, and
  skips that check with a clear message while Core is not deployed
  (`decisions.jsonl` was never written by anything, so every allowed
  connection used to trigger the auth.crit alert). It also surfaces
  token-burn failures from the audit log.
* `aegis-forget.py`: requires root (it deletes across uids; it had no
  check), rejects path-shaped subjects (`--subject ../../…` wrote outside
  the log tree), resolves symlinks before unlinking, deletes the removed
  documents' vector-db points by sha256 filter over REST (the old version
  printed "run the collection delete yourself"), and states plainly that
  matching is by filename.

## 11. Timers and verification

* `$EXIT_STATUS` in `ExecStopPost` was unbraced and unescaped — systemd
  expanded it to empty, `test -eq` became a syntax error, and the
  auth.crit alert fired on every run including successes. Fixed with
  `$$EXIT_STATUS` string comparison (also correct for signal names).
* `verify-egress.sh`: bypass tests probe an IP literal (they used to
  "pass" merely because DNS was broken); test 5 no longer mints a real
  root-owned approval token + live Anthropic call from a weekly timer
  (`AEGIS_VERIFY_NONINTERACTIVE=1`, set by the unit) and its assertions
  can actually fail now (`000`-aware; the old `[[ -n "$first" ]]` was
  always true); the listener whitelist includes systemd-resolved's stubs
  (127.0.0.53/54) and Tailscale's local API, which used to raise a false
  "PRIVACY BOUNDARY FAILED" every Monday; the summary reports
  passed/failed/skipped instead of the hardcoded "8/8" the docs quoted.
* `run-all.sh`: `bash -n` ran on ONE file (extra args are positional
  parameters) — now a loop over every script; the nft check renders the
  template and, off-box, fails only on non-"user does not exist" errors
  instead of always failing; new `tests/test_ingest.py` (chunker contract,
  sidecar filtering, mocked embed→upsert, infra-failure semantics) and a
  burn-denied test in `test_gate.py`.

## 12. Permissions and logs

* `/aegis/config` is 0755 (it holds policy, never secrets — rule 1) with
  per-file groups: the gate could never actually read `egress.toml` before
  (0750 root:aegis-core directory + an ACL that phase order guaranteed was
  never applied), and agents could never read their own manifests.
* `/aegis/logs/egress` is setgid `aegis-operators`: the README's daily
  `jq` over the audit log used to require root.
* logrotate `create` owners match the writing uid per directory (the old
  blanket `root root` broke Core's log after first rotation);
  `/aegis/logs/llm` exists for the runtime (its unit could not write the
  old shared `/aegis/logs`).
* `cp -r` install steps replaced with `rsync -a --delete` (re-running the
  old 03/07 nested `egress_gate/egress_gate/…` and reverted the sed'd
  username).
* Preflight no longer hard-fails on tools phase 02 installs (it used to
  fail a clean box for lacking `nft`, which it was about to install), and
  the stale "decision C4" label is now D1, matching docs/part-04 and
  DECISIONS.md.

## 13. Consciously NOT changed

* **No MITM in the Gate** — domain-level control + approval stays the
  boundary; payload review belongs in the release step (part-07 stance).
* **OCR** still fails closed with a clear message; wiring local OCR is a
  phase-4+ choice (tesseract vs. something heavier) worth its own
  decision.
* **auditd** stays enabled with default rules; a real audit policy is its
  own project (flagged, not faked).
* **Docker's rules are not managed** — we stopped destroying them; if
  Docker restarts it re-inserts its own.
* **Alerting still lands in the local journal** (auth.crit). Part-17
  phase 8 requires an independent channel; that is an operational choice
  (mail relay? ntfy? SMS gateway?) the installer cannot make for you — it
  is called out here instead of pretending `logger` reaches your phone.
* **Core task loop / agent tools / voice / home automation** — phases
  5-10, by the project's own roadmap.
