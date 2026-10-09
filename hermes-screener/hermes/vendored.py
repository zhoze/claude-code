"""Import the vendored screen packages unmodified, each in its own module namespace.

screens/fundamental and screens/ta both use flat imports (`import screen_lib`,
`import run_screens`, ...) and both define modules with those same names. Some
vendored functions also import lazily at call time (e.g. ta catalog.ranked imports
`evidence` and `screens_*`). So every use of a package goes through `using(pkg)`.
While the context is active, that package's directory is on sys.path and its
stashed modules are mapped in sys.modules. On exit, every flat module from that
directory is stashed again and removed, so the two packages never collide.
"""
from __future__ import annotations

import importlib
import os
import sys
import threading
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace

from .config import ROOT

SCREENS = os.path.join(ROOT, "screens")
DIRS = {"fund": os.path.join(SCREENS, "fundamental"), "ta": os.path.join(SCREENS, "ta"),
        "blend": os.path.join(SCREENS, "blend")}
_lock = threading.RLock()
_stash: dict[str, dict[str, ModuleType]] = {k: {} for k in DIRS}


def _from_dir(mod, pkg_dir) -> bool:
    f = getattr(mod, "__file__", None) or ""
    return bool(f) and os.path.dirname(os.path.abspath(f)) == pkg_dir


@contextmanager
def using(pkg: str):
    pkg_dir = DIRS[pkg]
    with _lock:
        saved = {n: sys.modules[n] for n in _stash[pkg] if n in sys.modules}
        sys.modules.update(_stash[pkg])
        sys.path.insert(0, pkg_dir)
        try:
            yield
        finally:
            sys.path.remove(pkg_dir)
            for name, mod in list(sys.modules.items()):
                if mod is not None and _from_dir(mod, pkg_dir):
                    _stash[pkg][name] = mod
                    del sys.modules[name]
            sys.modules.update(saved)


def _load(pkg: str, names: list[str]) -> SimpleNamespace:
    with using(pkg):
        return SimpleNamespace(**{n: importlib.import_module(n) for n in names})


class _Proxy:
    """Attribute access returns module proxies whose callables run inside using(pkg)."""

    def __init__(self, pkg: str, ns):
        self._pkg, self._ns = pkg, ns

    def __getattr__(self, name):
        obj = getattr(self._ns, name)
        if isinstance(obj, ModuleType):
            return _Proxy(self._pkg, obj)
        if callable(obj) and not isinstance(obj, type):
            pkg = self._pkg

            def call(*a, **kw):
                with using(pkg):
                    return obj(*a, **kw)
            return call
        return obj


_cache: dict[str, _Proxy] = {}


def fundamental() -> _Proxy:
    if "fund" not in _cache:
        d = DIRS["fund"]
        names = ["screen_lib", "build_screen_inputs"]
        names += sorted(f[:-3] for f in os.listdir(d)
                        if f.startswith("screen_") and f.endswith(".py") and f[:-3] not in names)
        _cache["fund"] = _Proxy("fund", _load("fund", names))
    return _cache["fund"]


def ta() -> _Proxy:
    if "ta" not in _cache:
        _cache["ta"] = _Proxy("ta", _load("ta", ["panel", "screen_lib", "catalog", "evidence",
                                                 "run_screens"]))
    return _cache["ta"]


def blend(variant: str = "decile10") -> _Proxy:
    key = f"blend_{variant}"
    if key not in _cache:
        fname = {"decile10": "blend_screen_decile10", "top5": "blend_screen_top5"}[variant]
        _cache[key] = _Proxy("blend", getattr(_load("blend", [fname]), fname))
    return _cache[key]
