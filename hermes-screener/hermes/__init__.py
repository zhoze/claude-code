"""Hermes stock-screening agent: a deterministic engine plus a fixed-order orchestrator.

The engine computes every number and the orchestrator calls its tools in the spec §10
order. No component here can place orders (spec I6).
"""
__version__ = "0.1.0"
