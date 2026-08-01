#!/usr/bin/env python3
"""
AEGIS Egress Gate
=================

The single point through which anything inside AEGIS may reach the internet.

Design intent
-------------
The Gate is *not* a convenience feature and it is *not* configurable by AEGIS
Core. It runs as its own system user (`aegis-proxy`), reads its own config,
and is the only uid on the box permitted to open outbound sockets to the
public internet (enforced by nftables, see config/nftables/aegis.nft).

Consequences of that design, which are the whole point:

  * AEGIS Core cannot make an unrouted call, because the kernel drops it.
  * A prompt-injected agent cannot talk itself past the Gate, because the
    Gate does not read model output. It reads a config file and an approval
    queue that Core cannot write to.

A note on credentials: the cloud API keys are STAGED with this unit
(systemd-creds, ImportCredential=) so that they live under the uid Core
cannot read. The Gate itself never injects them — a CONNECT tunnel is opaque
TLS and structurally cannot add headers. The component that eventually makes
cloud calls (the phase-5+ release step) receives them here, not in Core.

Route classes
-------------
  auto    Always allowed. For traffic that carries no private data outbound:
          package mirrors, model weight downloads, NTP-adjacent services.
  gated   Allowed only against a valid, unconsumed approval token. This is
          the class every cloud-inference endpoint belongs to. One token
          authorises ONE connection (which may carry keep-alive requests
          until it closes or hits the byte cap), then it is burned.
  deny    Anything not listed. There is no implicit allow.

Approval
--------
Tokens are minted out of band by `aegis-approve` (see approve.py), which runs
as *you*, not as AEGIS. The Gate only ever reads the queue directory. Core has
no write permission on it. This is what makes "on my explicit request" a
property of the system rather than a promise from a model.

Not implemented here on purpose
-------------------------------
TLS interception. The Gate sees the CONNECT host and the byte count, not the
payload. Inspecting payloads would mean terminating TLS to Anthropic and
storing your traffic in plaintext on the box — a worse security position than
the one it would be defending. Domain-level control plus mandatory approval is
the boundary. If you later want payload review, do it *before* the request is
made, in the release step, not by MITMing the transport.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import pwd
import re
import signal
import socket
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

CONFIG_PATH = Path(os.environ.get("AEGIS_GATE_CONFIG", "/aegis/config/egress.toml"))
APPROVAL_DIR = Path(os.environ.get("AEGIS_APPROVAL_DIR", "/aegis/run/approvals"))
AUDIT_LOG = Path(os.environ.get("AEGIS_AUDIT_LOG", "/aegis/logs/egress/audit.jsonl"))

RouteClass = Literal["auto", "gated", "deny"]

# A hostname we will accept in a CONNECT line. Deliberately strict: no
# underscores, no trailing dot, and no IP literals — IP-literal routes are
# unsupported by design (an IP entry in the config is rejected at load).
# Name the host; naming is what makes the audit log readable.
_HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


@dataclass(frozen=True)
class Route:
    host: str
    klass: RouteClass
    port: int = 443
    note: str = ""
    # Which service users may use this route at all. Empty means "any user
    # that can reach the Gate", which after nftables means low-trust agents
    # and Core. High-trust agents cannot reach the Gate at all.
    allow_uids: frozenset[str] = field(default_factory=frozenset)


@dataclass
class GateConfig:
    listen_host: str = "127.0.0.1"
    listen_port: int = 3128
    routes: dict[str, Route] = field(default_factory=dict)
    max_request_bytes: int = 32 * 1024 * 1024
    connect_timeout: float = 15.0
    idle_timeout: float = 300.0

    @classmethod
    def load(cls, path: Path) -> "GateConfig":
        with path.open("rb") as fh:
            raw = tomllib.load(fh)

        gate = raw.get("gate", {})
        cfg = cls(
            listen_host=gate.get("listen_host", "127.0.0.1"),
            listen_port=int(gate.get("listen_port", 3128)),
            max_request_bytes=int(gate.get("max_request_bytes", 32 * 1024 * 1024)),
            connect_timeout=float(gate.get("connect_timeout", 15.0)),
            idle_timeout=float(gate.get("idle_timeout", 300.0)),
        )

        for entry in raw.get("route", []):
            host = entry["host"].strip().lower()
            klass = entry.get("class", "deny")
            if klass not in ("auto", "gated", "deny"):
                raise ValueError(f"route {host}: bad class {klass!r}")
            if not _HOSTNAME_RE.match(host):
                raise ValueError(f"route {host}: not a valid hostname")
            cfg.routes[host] = Route(
                host=host,
                klass=klass,
                port=int(entry.get("port", 443)),
                note=entry.get("note", ""),
                allow_uids=frozenset(entry.get("allow_uids", [])),
            )
        return cfg

    def lookup(self, host: str) -> Route | None:
        """Exact match only. No wildcards, no suffix matching.

        Suffix matching is how allowlists leak: an entry for `anthropic.com`
        that also matches `evil-anthropic.com` or an attacker-controlled
        subdomain is a hole. If you need three subdomains, write three lines.
        """
        return self.routes.get(host.lower())


# --------------------------------------------------------------------------
# Approval queue
# --------------------------------------------------------------------------


class ApprovalQueue:
    """Single-use tokens minted outside AEGIS.

    Layout: one JSON file per token in APPROVAL_DIR (mode 2770,
    root:aegis-operators). Tokens are 0640, owned by the operator, group
    aegis-operators — the Gate reads and unlinks them via its supplementary
    membership of that group. The Gate consumes (unlinks) a token before it
    opens the upstream connection, so a crash mid-request burns the token
    rather than leaving it reusable.

    A token the Gate cannot BURN is treated as no token at all: serving a
    request while leaving the token in place would make it multi-use, which
    is the property this queue exists to prevent. That failure is loud — it
    means the directory permissions are wrong (see bootstrap/01).
    """

    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self.log = logging.getLogger("aegis.gate.approvals")
        # Set when the most recent consume() found a valid token but could
        # not unlink it. handle() reads this to audit the real reason.
        self.burn_denied = False

    def _candidates(self, host: str) -> list[Path]:
        if not self.dir.is_dir():
            return []
        out = []
        for path in sorted(self.dir.glob("*.json")):
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("host", "").lower() != host.lower():
                continue
            if float(data.get("expires_at", 0)) < time.time():
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    self.log.error(
                        "cannot reap expired token %s: %s — check that "
                        "%s is mode 2770 root:aegis-operators and that "
                        "aegis-proxy has the aegis-operators supplementary "
                        "group", path.name, exc, self.dir)
                continue
            out.append(path)
        return out

    def consume(self, host: str) -> dict | None:
        self.burn_denied = False
        for path in self._candidates(host):
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue               # raced or corrupt; try the next
            try:
                path.unlink()          # burn it before the connection opens
                return data
            except FileNotFoundError:
                continue               # someone else took it; try the next
            except OSError as exc:
                # Fail closed, loudly. A consumable-but-unburnable token is a
                # permissions bug that would silently turn single-use into
                # multi-use if we proceeded.
                self.burn_denied = True
                self.log.error(
                    "TOKEN BURN DENIED for %s (%s): %s — refusing the "
                    "request. Fix the approvals directory permissions "
                    "(bootstrap/01-users-and-dirs.sh) and re-approve.",
                    host, path.name, exc)
                return None
        return None


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------


class Audit:
    """Append-only JSONL. Every decision, allowed or denied.

    This is the explainability requirement from Part 01 made concrete, and it
    is your incident-response evidence. Ship it somewhere off-box on a
    schedule; a log that lives only on the compromised machine is not
    evidence.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self._log = logging.getLogger("aegis.gate.audit")
        self.healthy = True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.healthy = False
            self._log.error("audit directory unusable: %s", exc)

    async def write(self, **fields) -> bool:
        """Returns False if the record could not be persisted.

        Callers must treat False as a denial condition. An egress decision
        that cannot be recorded has not been made — allowing traffic we
        cannot account for is the failure this whole design exists to
        prevent, and it is exactly the state an attacker would engineer by
        filling the disk.
        """
        record = {"ts": time.time(), "iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **fields}
        line = json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
        async with self._lock:
            try:
                await asyncio.to_thread(self._append, line)
            except OSError as exc:
                if self.healthy:
                    # Log the transition once, not on every request.
                    self._log.error("AUDIT WRITE FAILED — denying all egress: %s", exc)
                self.healthy = False
                return False
            if not self.healthy:
                self._log.warning("audit log recovered")
            self.healthy = True
            return True

    def _append(self, line: str) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())


# --------------------------------------------------------------------------
# Peer identification
# --------------------------------------------------------------------------


def peer_username(sock: socket.socket) -> str | None:
    """Resolve the uid on the other end of a loopback TCP connection.

    Uses SO_PEERCRED, which the kernel fills in for AF_UNIX. For AF_INET on
    loopback it is not available portably, so we fall back to parsing
    /proc/net/tcp. That is why the Gate binds loopback only — the identity of
    the caller is part of the policy and we will not serve a peer we cannot
    name.
    """
    try:
        peer = sock.getpeername()
        local = sock.getsockname()
    except OSError:
        return None

    uid = _uid_from_proc_net_tcp(peer, local)
    if uid is None:
        return None
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def _uid_from_proc_net_tcp(peer: tuple, local: tuple) -> int | None:
    # peer is the client's (addr, port); in /proc/net/tcp that is the local
    # end of the client's socket, so we search for local_address == peer.
    want_local = _hexaddr(peer[0], peer[1])
    want_remote = _hexaddr(local[0], local[1])
    for proc in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(proc, "r", encoding="ascii") as fh:
                next(fh, None)
                for line in fh:
                    parts = line.split()
                    if len(parts) < 8:
                        continue
                    if parts[1].upper().endswith(want_local) and parts[2].upper().endswith(want_remote):
                        return int(parts[7])
        except OSError:
            continue
    return None


def _hexaddr(addr: str, port: int) -> str:
    try:
        packed = socket.inet_aton(addr)
    except OSError:
        return f":{port:04X}"
    as_int = int.from_bytes(packed, "little")
    return f"{as_int:08X}:{port:04X}"


# --------------------------------------------------------------------------
# Proxy
# --------------------------------------------------------------------------


class EgressGate:
    def __init__(self, cfg: GateConfig) -> None:
        self.cfg = cfg
        self.approvals = ApprovalQueue(APPROVAL_DIR)
        self.audit = Audit(AUDIT_LOG)
        self.log = logging.getLogger("aegis.gate")

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        sock = writer.get_extra_info("socket")
        user = peer_username(sock) if sock else None

        try:
            line = await asyncio.wait_for(reader.readline(), timeout=10.0)
        except asyncio.TimeoutError:
            await self._reject(writer, 408, "request timeout")
            return

        request = line.decode("latin-1", errors="replace").strip()

        # Drain headers; we do not forward them, CONNECT has no body.
        while True:
            try:
                hdr = await asyncio.wait_for(reader.readline(), timeout=10.0)
            except asyncio.TimeoutError:
                break
            if hdr in (b"\r\n", b"\n", b""):
                break

        parts = request.split()
        if len(parts) != 3 or parts[0].upper() != "CONNECT":
            await self.audit.write(event="reject", reason="non_connect",
                                   user=user, request=request[:200])
            await self._reject(writer, 405, "the Gate speaks CONNECT only")
            return

        target = parts[1]
        host, _, port_s = target.rpartition(":")
        if not host:
            await self._reject(writer, 400, "malformed target")
            return
        try:
            port = int(port_s)
        except ValueError:
            await self._reject(writer, 400, "malformed port")
            return

        # The caller's identity is part of the policy. A peer we cannot name
        # gets nothing — not even routes with an empty allow_uids list.
        if user is None:
            await self.audit.write(event="deny", reason="unidentified_peer",
                                   user=None, host=host, port=port)
            await self._reject(writer, 403, "caller identity could not be established")
            return

        route = self.cfg.lookup(host)

        if route is None or route.klass == "deny":
            await self.audit.write(event="deny", reason="not_allowlisted",
                                   user=user, host=host, port=port)
            await self._reject(writer, 403, f"{host} is not on the allowlist")
            return

        if port != route.port:
            await self.audit.write(event="deny", reason="port_mismatch",
                                   user=user, host=host, port=port,
                                   expected=route.port)
            await self._reject(writer, 403, "port not permitted for this host")
            return

        if route.allow_uids and (user or "") not in route.allow_uids:
            await self.audit.write(event="deny", reason="uid_not_permitted",
                                   user=user, host=host)
            await self._reject(writer, 403, "caller not permitted for this route")
            return

        approval = None
        if route.klass == "gated":
            approval = self.approvals.consume(host)
            if approval is None:
                if self.approvals.burn_denied:
                    await self.audit.write(event="error",
                                           reason="token_unlink_denied",
                                           user=user, host=host)
                    await self._reject(
                        writer, 503,
                        "approval present but the Gate cannot burn it — "
                        "approvals directory permissions are wrong; see the "
                        "gate log")
                    return
                await self.audit.write(event="deny", reason="no_approval",
                                       user=user, host=host)
                await self._reject(
                    writer, 403,
                    f"{host} requires approval. Run: aegis-approve {host} --reason '...'")
                return

        await self._relay(reader, writer, host, port, user, route, approval)

    async def _relay(self, reader, writer, host, port, user, route, approval) -> None:
        started = time.time()
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(host=host, port=port),
                timeout=self.cfg.connect_timeout,
            )
        except (OSError, asyncio.TimeoutError) as exc:
            await self.audit.write(event="error", reason="upstream_unreachable",
                                   user=user, host=host, detail=str(exc))
            await self._reject(writer, 502, "upstream unreachable")
            return

        recorded = await self.audit.write(
            event="allow", user=user, host=host, port=port,
            klass=route.klass,
            approval_id=(approval or {}).get("id"),
            approval_reason=(approval or {}).get("reason"),
        )
        if not recorded:
            # Fail closed. We hold an approval token that has already been
            # burned, which is the safe direction: the operator re-approves
            # once the log is writable again.
            with contextlib.suppress(Exception):
                up_writer.close()
            await self._reject(writer, 503,
                               "egress denied: audit log unwritable (check disk on /aegis/logs)")
            return

        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()

        sent = await self._pump(reader, up_writer, up_reader, writer)

        await self.audit.write(event="close", user=user, host=host,
                               bytes_out=sent, duration=round(time.time() - started, 3))

    async def _pump(self, c_read, u_write, u_read, c_write) -> int:
        counter = {"out": 0}

        async def copy(src, dst, count: bool) -> None:
            try:
                while True:
                    chunk = await asyncio.wait_for(src.read(65536),
                                                   timeout=self.cfg.idle_timeout)
                    if not chunk:
                        break
                    if count:
                        counter["out"] += len(chunk)
                        if counter["out"] > self.cfg.max_request_bytes:
                            raise ConnectionAbortedError("outbound byte cap exceeded")
                    dst.write(chunk)
                    await dst.drain()
            except (asyncio.TimeoutError, ConnectionError, OSError):
                pass
            finally:
                with contextlib.suppress(Exception):
                    dst.close()

        await asyncio.gather(
            copy(c_read, u_write, True),
            copy(u_read, c_write, False),
        )
        return counter["out"]

    async def _reject(self, writer, code: int, message: str) -> None:
        body = message.encode()
        writer.write(
            f"HTTP/1.1 {code} {message}\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n\r\n".encode() + body
        )
        with contextlib.suppress(Exception):
            await writer.drain()
            writer.close()

    async def serve(self) -> None:
        server = await asyncio.start_server(
            self.handle, self.cfg.listen_host, self.cfg.listen_port,
        )
        addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
        self.log.info("egress gate listening on %s with %d routes",
                      addrs, len(self.cfg.routes))
        for route in sorted(self.cfg.routes.values(), key=lambda r: r.host):
            self.log.info("  %-40s %s", route.host, route.klass)
        async with server:
            await server.serve_forever()


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("AEGIS_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )
    log = logging.getLogger("aegis.gate")

    if os.geteuid() == 0:
        log.error("refusing to run as root; the Gate runs as aegis-proxy")
        return 1

    try:
        cfg = GateConfig.load(CONFIG_PATH)
    except Exception as exc:
        log.error("config %s is unusable: %s", CONFIG_PATH, exc)
        return 1

    if not cfg.routes:
        log.warning("no routes configured — the Gate will deny everything")

    # The approvals directory is created by bootstrap/01 with the exact
    # ownership the security model needs (2770 root:aegis-operators). The
    # Gate must not create it — a directory this process owns is one this
    # process could repopulate.
    if not APPROVAL_DIR.is_dir():
        log.error("approvals directory %s is missing — run "
                  "bootstrap/01-users-and-dirs.sh. Gated routes will deny "
                  "until it exists.", APPROVAL_DIR)

    gate = EgressGate(cfg)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, loop.stop)
    try:
        loop.run_until_complete(gate.serve())
    except (KeyboardInterrupt, RuntimeError):
        pass
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
