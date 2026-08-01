"""AEGIS Core — orchestration above the trust boundary.

Nothing in this package is security-critical. Enforcement lives in
config/nftables/aegis.nft (kernel) and src/egress_gate/gate.py (userspace).
If you are about to add a security check here, read
docs/part-02-architecture.md design rule 7 first.
"""
__version__ = "0.1.0"
