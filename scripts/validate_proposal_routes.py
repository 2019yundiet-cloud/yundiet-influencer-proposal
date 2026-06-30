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
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    if not isinstance(registry, dict):
        raise ValueError("registry: top-level JSON value must be an object")
    return registry


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
    if not isinstance(registry.get("base_url"), str) or not registry.get("base_url", "").startswith("https://"):
        failures.append("registry: base_url must be an https URL")

    routes = registry.get("routes")
    if not isinstance(routes, list) or not routes:
        failures.append("registry: routes must be a non-empty list")
        return failures

    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    seen_local_files: set[str] = set()
    root_routes = 0
    for route in routes:
        if not isinstance(route, dict):
            failures.append("registry: every route must be an object")
            continue

        raw_route_id = route.get("id", "")
        route_id = raw_route_id if isinstance(raw_route_id, str) else ""
        route_label = route_id or "registry route"
        raw_route_path = route.get("path", "")
        route_path = raw_route_path if isinstance(raw_route_path, str) else ""
        local_file = route.get("local_file", "")
        if not isinstance(raw_route_id, str) or not route_id:
            failures.append("registry: route id must be a non-empty string")
        elif route_id in seen_ids:
            failures.append(f"registry: duplicate id {route_id!r}")
        if route_id:
            seen_ids.add(route_id)

        if not isinstance(raw_route_path, str) or not route_path:
            failures.append(f"{route_label}: path must be a non-empty string")
        else:
            if not route_path.startswith("/"):
                failures.append(f"{route_label}: path must start with /")
            if route_path != "/" and not route_path.endswith("/"):
                failures.append(f"{route_label}: path must end with /")
            if route_path != "/" and route_path != f"/{route_path.strip('/')}/":
                failures.append(f"{route_label}: path must be normalized with one leading and trailing slash")
            if route_path in seen_paths:
                failures.append(f"registry: duplicate path {route_path!r}")
            seen_paths.add(route_path)

        if route.get("locked") is not True:
            failures.append(f"{route_label}: route must stay locked once published")

        if not isinstance(local_file, str) or not local_file:
            failures.append(f"{route_label}: local_file required")
        else:
            local_path = Path(local_file)
            if local_path.is_absolute() or ".." in local_path.parts:
                failures.append(f"{route_label}: local_file must stay inside the Pages repository")
            if local_file in seen_local_files:
                failures.append(f"registry: duplicate local_file {local_file!r}")
            seen_local_files.add(local_file)

        if route_path == "/":
            root_routes += 1
            if local_file != "index.html":
                failures.append("sponsorship root route must use index.html")
        elif local_file == "index.html":
            failures.append(f"{route_label}: non-root routes must not reuse root index.html")
        elif route_path:
            expected_local_file = f"{route_path.strip('/')}/index.html"
            if local_file != expected_local_file:
                failures.append(f"{route_label}: local_file must match route path as {expected_local_file!r}")

        for field in ("expected_markers", "forbidden_markers"):
            markers = route.get(field)
            if not isinstance(markers, list) or not markers or not all(isinstance(item, str) and item for item in markers):
                failures.append(f"{route_label}: {field} must be a non-empty list of strings")

        for field in ("owner", "purpose"):
            if not isinstance(route.get(field), str) or not route.get(field):
                failures.append(f"{route_label}: {field} required")

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
            except (HTTPError, URLError, RuntimeError, OSError) as error:
                last_error = str(error)
            if attempt < retries:
                time.sleep(wait_seconds)
        if last_error:
            failures.append(f"{label}: {last_error}")
    return failures


def validate_options(retries: int, wait_seconds: int, timeout: int) -> list[str]:
    failures: list[str] = []
    if retries < 0:
        failures.append("options: retries must be >= 0")
    if wait_seconds < 0:
        failures.append("options: wait-seconds must be >= 0")
    if timeout <= 0:
        failures.append("options: timeout must be > 0")
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

    failures = validate_options(args.retries, args.wait_seconds, args.timeout)
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1

    try:
        registry = load_registry()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL registry: {error}", file=sys.stderr)
        return 1

    failures = validate_registry_shape(registry)
    if not failures:
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
