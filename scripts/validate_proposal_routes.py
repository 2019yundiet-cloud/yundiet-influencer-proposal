#!/usr/bin/env python3
"""Validate proposal-page route ownership and content markers."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "proposal-link-registry.json"


def load_registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def route_url(base_url: str, route_path: str) -> str:
    base = base_url.rstrip("/")
    path = "/" if route_path == "/" else f"/{route_path.strip('/')}/"
    return f"{base}{path}"


def read_public(url: str, timeout: int) -> str:
    request = Request(url, headers={"User-Agent": "proposal-route-validator/1.0"})
    with urlopen(request, timeout=timeout) as response:
        status = getattr(response, "status", 200)
        if status != 200:
            raise RuntimeError(f"HTTP {status}")
        return response.read().decode("utf-8", errors="replace")


def validate_text(route: dict, text: str, label: str) -> list[str]:
    failures: list[str] = []
    for marker in route.get("expected_markers", []):
        if marker not in text:
            failures.append(f"{label}: missing expected marker {marker!r}")
    for marker in route.get("forbidden_markers", []):
        if marker in text:
            failures.append(f"{label}: found forbidden marker {marker!r}")
    return failures


def validate_registry_shape(registry: dict) -> list[str]:
    failures: list[str] = []
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    root_routes = 0
    for route in registry.get("routes", []):
        route_id = route.get("id", "")
        route_path = route.get("path", "")
        local_file = route.get("local_file", "")
        if not route_id:
            failures.append("registry: route missing id")
        elif route_id in seen_ids:
            failures.append(f"registry: duplicate id {route_id!r}")
        seen_ids.add(route_id)

        if not route_path.startswith("/"):
            failures.append(f"{route_id}: path must start with /")
        if route_path != "/" and not route_path.endswith("/"):
            failures.append(f"{route_id}: path must end with /")
        if route_path in seen_paths:
            failures.append(f"registry: duplicate path {route_path!r}")
        seen_paths.add(route_path)

        if route_path == "/":
            root_routes += 1
            if not route.get("locked"):
                failures.append("sponsorship root route must stay locked")
            if local_file != "index.html":
                failures.append("sponsorship root route must use index.html")
        elif local_file == "index.html":
            failures.append(f"{route_id}: non-root routes must not reuse root index.html")

        if not route.get("expected_markers"):
            failures.append(f"{route_id}: expected_markers required")
        if not route.get("forbidden_markers"):
            failures.append(f"{route_id}: forbidden_markers required")

    if root_routes != 1:
        failures.append(f"registry: expected exactly one root route, found {root_routes}")
    return failures


def validate_local(registry: dict) -> list[str]:
    failures: list[str] = []
    for route in registry.get("routes", []):
        local_path = ROOT / route["local_file"]
        label = f"{route['id']} local {route['local_file']}"
        if not local_path.exists():
            failures.append(f"{label}: file missing")
            continue
        text = local_path.read_text(encoding="utf-8", errors="replace")
        failures.extend(validate_text(route, text, label))
    return failures


def validate_public(registry: dict, retries: int, wait_seconds: int, timeout: int) -> list[str]:
    failures: list[str] = []
    base_url = registry["base_url"]
    for route in registry.get("routes", []):
        url = route_url(base_url, route["path"])
        label = f"{route['id']} public {url}"
        last_error = ""
        for attempt in range(retries + 1):
            try:
                text = read_public(url, timeout)
                route_failures = validate_text(route, text, label)
                if not route_failures:
                    last_error = ""
                    break
                last_error = "; ".join(route_failures)
            except (HTTPError, URLError, RuntimeError) as error:
                last_error = str(error)
            if attempt < retries:
                time.sleep(wait_seconds)
        if last_error:
            failures.append(f"{label}: {last_error}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true", help="validate local files")
    parser.add_argument("--public", action="store_true", help="validate published GitHub Pages routes")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--wait-seconds", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=10)
    args = parser.parse_args()

    if not args.local and not args.public:
        args.local = True

    registry = load_registry()
    failures = validate_registry_shape(registry)
    if args.local:
        failures.extend(validate_local(registry))
    if args.public:
        failures.extend(validate_public(registry, args.retries, args.wait_seconds, args.timeout))

    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1

    checked = []
    if args.local:
        checked.append("local")
    if args.public:
        checked.append("public")
    print(f"proposal routes ok: {', '.join(checked)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
