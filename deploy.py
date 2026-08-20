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


# ---------------------------------------------------------------------- panel
# Перенесено з abyss/tools/remote.py (Panel) — Pterodactyl client API, stdlib.


class PanelError(RuntimeError):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code


class Panel:
    def __init__(self, url: str, server: str, token: str):
        self.panel = url.rstrip("/")
        self.server = server
        self.base = f"{self.panel}/api/client/servers/{server}"
        self.token = token

    def _req(self, method: str, path: str, body=None):
        url = self.base + path
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                payload = r.read()
        except urllib.error.HTTPError as e:
            raise PanelError(e.code, f"{method} {path} -> HTTP {e.code}")
        except urllib.error.URLError as e:
            die(f"cannot reach the panel: {e.reason}")
        if not payload:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return payload

    def _signed(self, kind: str, query: str = "") -> str:
        return self._req("GET", f"/files/{kind}{query}")["attributes"]["url"]

    def state(self) -> str:
        try:
            return self._req("GET", "/resources")["attributes"]["current_state"]
        except PanelError:
            return "unknown"

    def power(self, signal: str) -> None:
        self._req("POST", "/power", {"signal": signal})

    def listdir(self, directory: str) -> list[dict]:
        q = urllib.parse.urlencode({"directory": directory})
        return [e["attributes"] for e in self._req("GET", f"/files/list?{q}")["data"]]

    def read(self, path: str) -> bytes:
        url = self._signed("download", "?" + urllib.parse.urlencode({"file": path}))
        with urllib.request.urlopen(url, timeout=180) as r:
            return r.read()

    def upload(self, local: Path, remote_dir: str) -> None:
        url = self._signed("upload") + "&" + urllib.parse.urlencode({"directory": remote_dir})
        boundary = "----mcdeploy" + uuid.uuid4().hex
        head = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="files"; filename="{local.name}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        payload = head + local.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=300).read()
        except urllib.error.HTTPError as e:
            raise PanelError(e.code, f"upload {local.name} -> HTTP {e.code}")

    def delete(self, root: str, names: list[str]) -> None:
        self._req("POST", "/files/delete", {"root": root, "files": names})

    def tail(self, lines: int) -> list[str]:
        try:
            text = self.read(REMOTE_LOG).decode("utf-8", "replace")
        except PanelError as e:
            if e.code == 404:
                return []
            raise
        return text.splitlines()[-lines:]


# ----------------------------------------------------------------------- flow


def wait_for_boot(panel: Panel, boot_ok: str, before: int) -> bool:
    """Полінг лога до boot-ok/boot-bad. ~3 хв (36×5с). Лог ротується на рестарті."""
    for attempt in range(36):
        time.sleep(5)
        print(".", end="", flush=True)
        try:
            lines = panel.tail(4000)
        except PanelError:
            continue
        if len(lines) < before:
            before = 0  # ротація
        fresh = lines[before:]
        if any(boot_ok in ln for ln in fresh):
            print(flush=True)
            return True
        if any(bad in ln for ln in fresh for bad in BOOT_BAD):
            print(flush=True)
            return False
    print(flush=True)
    return False


def deploy_to_server(panel: Panel, alias: str, jars: list[Path],
                     rules, rule_idxs: list[int], boot_ok: str, dry: bool) -> None:
    """Всі джари цього сервера, потім ОДИН рестарт."""
    state = panel.state()
    for _ in range(36):  # чужий рестарт у процесі — дочекатись
        if state not in ("starting", "stopping"):
            break
        time.sleep(5)
        state = panel.state()
    remote = [e["name"] for e in panel.listdir(REMOTE_MODS) if e["name"].endswith(".jar")]
    for jar, idx in zip(jars, rule_idxs):
        stale = stale_for_rule(remote, rules, idx, keep=jar.name)
        print(f"  [{alias}] upload {jar.name} ({jar.stat().st_size // 1024} KB)"
              + (f"; remove {', '.join(stale)}" if stale else ""))
        if dry:
            continue
        panel.upload(jar, REMOTE_MODS)
        if stale:
            panel.delete(REMOTE_MODS, stale)
    if dry:
        print(f"  [{alias}] (dry run — no restart)")
        return
    before = len(panel.tail(4000))
    panel.power("restart")
    print(f"  [{alias}] restarting", end="", flush=True)
    if wait_for_boot(panel, boot_ok, before):
        print(f"  [{alias}] booted OK")
    else:
        die(f"[{alias}] no clean boot within 3 min — check the panel console; "
            f"redeploy the previous release via workflow_dispatch to roll back")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", required=True, type=Path)
    ap.add_argument("--targets", required=True)
    ap.add_argument("--servers", type=Path,
                    default=Path(__file__).resolve().parent / "servers.json")
    ap.add_argument("--boot-ok", default="Done (")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    url, token = os.environ.get("PTERO_URL"), os.environ.get("PTERO_TOKEN")
    if not url or not token:
        die("PTERO_URL / PTERO_TOKEN are not set (secrets missing? run sync-secrets in mc-deploy)")

    try:
        rules = parse_targets(args.targets)
        servers = json.loads(args.servers.read_text())
        assets = sorted(p.name for p in args.assets.glob("*.jar"))
        plan = plan_release(assets, rules)
    except (ValueError, OSError, json.JSONDecodeError) as e:
        die(str(e))

    unknown = sorted({alias for _, alias in rules} - set(servers))
    if unknown:
        die(f"unknown server alias(es) {unknown} — add them to servers.json in mc-deploy")

    by_server: dict[str, list[int]] = {}
    for idx, (_glob, alias) in enumerate(rules):
        by_server.setdefault(alias, []).append(idx)

    for alias, idxs in by_server.items():
        panel = Panel(url, servers[alias], token)
        jars = [args.assets / plan[i] for i in idxs]
        deploy_to_server(panel, alias, jars, rules, idxs, args.boot_ok, args.dry_run)
    print("done.")


if __name__ == "__main__":
    main()
