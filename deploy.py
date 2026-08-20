#!/usr/bin/env python3
"""Deploy released mod jars to Pterodactyl servers.

Узагальнення abyss/tools/remote.py `push-mod`. Stdlib only.
Викликається з reusable workflow (див. .github/workflows/deploy.yml):

    python3 deploy.py --assets assets/ --targets "<рядки>" [--boot-ok "Done ("] [--dry-run]

Env: PTERO_URL, PTERO_TOKEN. Аліаси серверів -> servers.json поряд зі скриптом.
Рядок targets:  <глоб>  ->  <аліас>     # перший глоб, що зматчив, забирає асет
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

REMOTE_MODS = "/mods"
REMOTE_LOG = "/logs/latest.log"
EXCLUDE_SUFFIXES = ("-sources.jar", "-javadoc.jar", "-dev.jar")
BOOT_BAD = (
    "Failed to load",
    "A potential crash",
    "Exception in server tick loop",
    "Mixin apply failed",
    "Incompatible mod set",
    "duplicate mod",
)


def die(msg: str) -> None:
    print(f"::error::{msg}", file=sys.stderr)
    sys.exit(1)


# ------------------------------------------------------------------ pure core


def parse_targets(text: str) -> list[tuple[str, str]]:
    rules: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "->" not in line:
            raise ValueError(f"bad targets line (need '<glob> -> <alias>'): {raw!r}")
        glob, alias = (p.strip() for p in line.split("->", 1))
        if not glob or not alias:
            raise ValueError(f"bad targets line: {raw!r}")
        rules.append((glob, alias))
    if not rules:
        raise ValueError("targets is empty")
    return rules


def route(names: list[str], rules: list[tuple[str, str]]) -> dict[int, list[str]]:
    """Перший глоб, що зматчив, забирає ім'я; службові суфікси відкинуто."""
    claimed: dict[int, list[str]] = {}
    for name in names:
        if name.endswith(EXCLUDE_SUFFIXES):
            continue
        for i, (glob, _alias) in enumerate(rules):
            if fnmatch.fnmatch(name, glob):
                claimed.setdefault(i, []).append(name)
                break
    return claimed


def plan_release(asset_names: list[str], rules: list[tuple[str, str]]) -> dict[int, str]:
    """Кожне правило має забрати РІВНО один асет релізу."""
    claimed = route(asset_names, rules)
    plan: dict[int, str] = {}
    for i, (glob, alias) in enumerate(rules):
        got = claimed.get(i, [])
        if len(got) != 1:
            raise ValueError(
                f"rule '{glob} -> {alias}' matched {len(got)} release assets "
                f"({got or 'none'}) — expected exactly 1"
            )
        plan[i] = got[0]
    return plan


def stale_for_rule(remote_names: list[str], rules: list[tuple[str, str]],
                   idx: int, keep: str) -> list[str]:
    """Джари на сервері, що належать цьому правилу (крім нового). Чужі не чіпаємо."""
    return [n for n in route(remote_names, rules).get(idx, []) if n != keep]
