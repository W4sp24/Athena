"""Strategy registry by module discovery (NFR-04): adding a strategy is one new file.

Any public module in ``cryptolab.strategies`` that defines a class with a string ``name``
attribute and a callable ``on_bar`` is registered under that name. Modules whose name
starts with ``_`` (and this module) are skipped.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from functools import cache
from typing import Any

from cryptolab.engine.types import Strategy

_PACKAGE = "cryptolab.strategies"


def discover(package: str = _PACKAGE) -> dict[str, type[Any]]:
    """Import every public module of ``package`` and map strategy ``name`` -> class."""
    pkg = importlib.import_module(package)
    found: dict[str, type[Any]] = {}
    for info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda i: i.name):
        if info.name.startswith("_") or info.name == "registry":
            continue
        module = importlib.import_module(f"{package}.{info.name}")
        for obj in vars(module).values():
            if not inspect.isclass(obj) or obj.__module__ != module.__name__:
                continue
            name = getattr(obj, "name", None)
            if not isinstance(name, str) or not callable(getattr(obj, "on_bar", None)):
                continue
            if name in found and found[name] is not obj:
                other = found[name].__module__
                raise ValueError(f"duplicate strategy name {name!r}: {other} and {module.__name__}")
            found[name] = obj
    return found


@cache
def _registry() -> dict[str, type[Any]]:
    return discover(_PACKAGE)


def available_strategies() -> list[str]:
    return sorted(_registry())


def get_strategy(name: str, **params: Any) -> Strategy:
    """Instantiate the strategy registered as ``name`` with constructor ``params``."""
    try:
        cls = _registry()[name]
    except KeyError:
        raise KeyError(f"unknown strategy {name!r}; available: {available_strategies()}") from None
    strategy: Strategy = cls(**params)
    return strategy
