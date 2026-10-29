"""Staff report mapping rules, one module per publishing format (see README.md).

Every module in this package exposes ``RULES = {"rule-name": rule}``, where a rule is
``rule(ctx: staff_reports.Context, **params) -> list[staff_reports.Mapping]`` and the name
describes the format, not an agency. The modules are discovered and merged into
``REGISTRY`` here; two modules registering the same name is an error.

Agencies choose a rule in ``packages/domain/agencies.json``, so an agency whose documents
follow a known format needs only configuration::

    "staffReports": {"rule": "ttd-packet", "footer": "(?:RTCPC\\s+)?AGENDA ITEM"}

A rule's parameters are its keyword-only arguments, spelled in camelCase in the entry
(``packet_name`` -> ``packetName``); those without a default are required. The annotation
says what a value may be: ``str`` (non-empty text), ``staff_reports.Regex`` (a regular
expression, compiled when the registry loads), ``int`` (a positive whole number, such as a
download limit), or ``Literal[...]`` (one of those values).
An agency without a rule may say why in a ``note``, with ``"published": false`` when it
publishes no reports a rule could use, or ``true`` when it does but they can't be reached
or matched yet. Every entry may carry a ``note``.
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil
import re
from functools import lru_cache, partial
from typing import Literal, get_args, get_origin, get_type_hints

from ..staff_reports import Regex


@lru_cache(maxsize=None)
def _params(name: str, rule) -> dict[str, tuple[str, object, bool]]:
    """``{configKey: (keyword, annotation, required)}`` for a rule's parameters, which follow ``ctx``."""
    hints = get_type_hints(rule)
    found = {}
    for keyword, parameter in list(inspect.signature(rule).parameters.items())[1:]:
        kind = hints.get(keyword)
        if parameter.kind is not parameter.KEYWORD_ONLY:
            raise TypeError(f"staff report rule {name}: parameters after ctx must be keyword-only")
        if kind not in (str, Regex, int) and get_origin(kind) is not Literal:
            raise TypeError(f"staff report rule {name}: parameter {keyword} needs a str, Regex, int, or Literal annotation")
        found[re.sub(r"_([a-z])", lambda m: m[1].upper(), keyword)] = (keyword, kind, parameter.default is parameter.empty)
    return found


def _discover() -> dict:
    merged: dict = {}
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        for name, rule in getattr(module, "RULES", {}).items():
            if name in merged:
                raise RuntimeError(f"staff report rule {name} is defined in more than one module ({info.name})")
            if not callable(rule) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
                raise TypeError(f"{info.name}.RULES[{name!r}] is not a callable with a lower-case hyphenated name")
            _params(name, rule)
            merged[name] = rule
    return merged


REGISTRY = _discover()


def check_config(agency: dict) -> None:
    """Raise ValueError when an agency's ``staffReports`` entry names no known rule, or gives its parameters wrongly."""
    config = agency.get("staffReports")
    if config is None:
        return
    where = f"staffReports of {agency.get('id')}"
    if not isinstance(config, dict) or ("note" in config and not (isinstance(config["note"], str) and config["note"].strip())):
        raise ValueError(f"invalid {where}")
    if "rule" not in config:
        if set(config) - {"published", "note"} or not isinstance(config.get("published", False), bool) or "note" not in config:
            raise ValueError(f"invalid {where}: give a rule, or a note on why there is none (and published, when known)")
        return
    if config["rule"] not in REGISTRY:
        raise ValueError(f"unknown staff report rule {config['rule']!r} in {where} (known: {', '.join(sorted(REGISTRY))})")
    spec = _params(config["rule"], REGISTRY[config["rule"]])
    given = {key: value for key, value in config.items() if key not in ("rule", "note")}
    if unknown := set(given) - set(spec):
        raise ValueError(f"{where}: rule {config['rule']} takes no {', '.join(sorted(unknown))}")
    if missing := {key for key, (_, _, required) in spec.items() if required} - set(given):
        raise ValueError(f"{where}: rule {config['rule']} needs {', '.join(sorted(missing))}")
    for key, value in given.items():
        kind = spec[key][1]
        if get_origin(kind) is Literal:
            if value not in get_args(kind):
                raise ValueError(f"{where}: {key} must be one of {', '.join(map(str, get_args(kind)))}")
        elif kind is int:
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{where}: {key} must be a positive whole number")
        elif not isinstance(value, str) or not value.strip():
            raise ValueError(f"{where}: {key} must be non-empty text")
        elif kind is Regex:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"{where}: {key} is not a regular expression ({exc})") from exc


@lru_cache(maxsize=1)
def agency_rules() -> dict:
    """``{agency ID: rule(ctx)}`` for every registry entry whose ``staffReports`` names a rule, with its parameters bound."""
    from ..catalog import agency_catalog  # the catalog validates entries against REGISTRY, so it loads after it
    bound = {}
    for agency in agency_catalog():
        config = agency.get("staffReports", {})
        if "rule" in config:
            spec = _params(config["rule"], REGISTRY[config["rule"]])
            bound[agency["id"]] = partial(REGISTRY[config["rule"]], **{spec[k][0]: v for k, v in config.items() if k in spec})
    return bound
