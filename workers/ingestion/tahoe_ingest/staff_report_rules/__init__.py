"""Staff report mapping rules, one module per agency or platform (see README.md).

Every module in this package exposes ``RULES = {"agency-id": rule}``, where a rule is
``rule(ctx: staff_reports.Context) -> list[staff_reports.Mapping]``. The modules are
discovered and merged here, so adding an agency means adding a module, with no shared
registry to edit. Two modules claiming the same agency ID is an error.
"""
from __future__ import annotations

import importlib
import pkgutil


def _discover() -> dict:
    merged: dict = {}
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        for agency, rule in getattr(module, "RULES", {}).items():
            if agency in merged:
                raise RuntimeError(f"staff report rule for {agency} is defined in more than one module ({info.name})")
            if not callable(rule):
                raise TypeError(f"{info.name}.RULES[{agency!r}] is not callable")
            merged[agency] = rule
    return merged


RULES = _discover()
