#!/usr/bin/env python3
"""Regression tests for proposal route validation."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().with_name("validate_proposal_routes.py")
SPEC = importlib.util.spec_from_file_location("validate_proposal_routes", SCRIPT)
assert SPEC and SPEC.loader
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def route(
    route_id: str,
    route_path: str,
    local_file: str,
    *,
    locked: bool = True,
) -> dict:
    return {
        "id": route_id,
        "path": route_path,
        "local_file": local_file,
        "owner": "test-owner",
        "purpose": "test proposal route",
        "locked": locked,
        "expected_markers": ["expected"],
        "forbidden_markers": ["forbidden"],
    }


class ProposalRouteValidatorTests(unittest.TestCase):
    def test_current_registry_shape_is_valid(self) -> None:
        registry = validator.load_registry()

        self.assertEqual(validator.validate_registry_shape(registry), [])

    def test_github_workflow_runs_route_validator_tests_for_test_changes(self) -> None:
        workflow = SCRIPT.parents[1] / ".github" / "workflows" / "validate-proposal-routes.yml"
        workflow_text = workflow.read_text(encoding="utf-8")

        self.assertGreaterEqual(workflow_text.count("scripts/test_validate_proposal_routes.py"), 3)
        self.assertIn("run: python3 scripts/test_validate_proposal_routes.py", workflow_text)

    def test_duplicate_local_file_is_rejected(self) -> None:
        registry = {
            "base_url": "https://example.test/proposals",
            "routes": [
                route("root", "/", "index.html"),
                route("partner-a", "/partner-a/", "partner-a/index.html"),
                route("partner-b", "/partner-b/", "partner-a/index.html"),
            ],
        }

        failures = validator.validate_registry_shape(registry)

        self.assertIn("registry: duplicate local_file 'partner-a/index.html'", failures)

    def test_local_file_must_match_route_path(self) -> None:
        registry = {
            "base_url": "https://example.test/proposals",
            "routes": [
                route("root", "/", "index.html"),
                route("partner-a", "/partner-a/", "wrong/index.html"),
            ],
        }

        failures = validator.validate_registry_shape(registry)

        self.assertIn("partner-a: local_file must match route path as 'partner-a/index.html'", failures)

    def test_local_file_path_escape_is_rejected(self) -> None:
        registry = {
            "base_url": "https://example.test/proposals",
            "routes": [
                route("root", "/", "index.html"),
                route("partner-a", "/partner-a/", "../index.html"),
            ],
        }

        failures = validator.validate_registry_shape(registry)

        self.assertIn("partner-a: local_file must stay inside the Pages repository", failures)

    def test_published_routes_must_stay_locked(self) -> None:
        registry = {
            "base_url": "https://example.test/proposals",
            "routes": [
                route("root", "/", "index.html"),
                route("partner-a", "/partner-a/", "partner-a/index.html", locked=False),
            ],
        }

        failures = validator.validate_registry_shape(registry)

        self.assertIn("partner-a: route must stay locked once published", failures)

    def test_route_id_and_path_types_fail_without_type_error(self) -> None:
        registry = {
            "base_url": "https://example.test/proposals",
            "routes": [
                route("root", "/", "index.html"),
                {
                    "id": ["partner-a"],
                    "path": 123,
                    "local_file": "partner-a/index.html",
                    "owner": "test-owner",
                    "purpose": "bad type route",
                    "locked": True,
                    "expected_markers": ["expected"],
                    "forbidden_markers": ["forbidden"],
                },
            ],
        }

        failures = validator.validate_registry_shape(registry)

        self.assertIn("registry: route id must be a non-empty string", failures)
        self.assertIn("registry route: path must be a non-empty string", failures)

    def test_locked_must_be_literal_true(self) -> None:
        bad_route = route("partner-a", "/partner-a/", "partner-a/index.html")
        bad_route["locked"] = "true"
        registry = {
            "base_url": "https://example.test/proposals",
            "routes": [
                route("root", "/", "index.html"),
                bad_route,
            ],
        }

        failures = validator.validate_registry_shape(registry)

        self.assertIn("partner-a: route must stay locked once published", failures)

    def test_markers_must_be_lists_of_strings(self) -> None:
        bad_route = route("partner-a", "/partner-a/", "partner-a/index.html")
        bad_route["expected_markers"] = "expected"
        registry = {
            "base_url": "https://example.test/proposals",
            "routes": [
                route("root", "/", "index.html"),
                bad_route,
            ],
        }

        failures = validator.validate_registry_shape(registry)

        self.assertIn("partner-a: expected_markers must be a non-empty list of strings", failures)

    def test_public_validation_options_must_attempt_at_least_once(self) -> None:
        failures = validator.validate_options(retries=-1, wait_seconds=0, timeout=10)

        self.assertIn("options: retries must be >= 0", failures)

    def test_public_validation_options_reject_invalid_wait_and_timeout(self) -> None:
        failures = validator.validate_options(retries=0, wait_seconds=-1, timeout=0)

        self.assertIn("options: wait-seconds must be >= 0", failures)
        self.assertIn("options: timeout must be > 0", failures)

    def test_main_rejects_invalid_public_options_before_registry_read(self) -> None:
        with (
            mock.patch.object(validator, "REGISTRY", Path("/tmp/missing-proposal-link-registry.json")),
            mock.patch(
                "sys.argv",
                [
                    "validate_proposal_routes.py",
                    "--public",
                    "--retries",
                    "-1",
                    "--wait-seconds",
                    "0",
                    "--timeout",
                    "10",
                ],
            ),
            mock.patch("sys.stderr"),
        ):
            exit_code = validator.main()

        self.assertEqual(exit_code, 1)

    def test_public_validation_reports_timeout_as_route_failure(self) -> None:
        registry = {
            "base_url": "https://example.test/proposals",
            "routes": [route("root", "/", "index.html")],
        }
        with mock.patch.object(validator, "read_public", side_effect=TimeoutError("timed out")):
            failures = validator.validate_public(registry, retries=0, wait_seconds=0, timeout=1)

        self.assertEqual(
            failures,
            ["root public https://example.test/proposals/: timed out"],
        )

    def test_public_validation_reports_os_errors_as_route_failure(self) -> None:
        registry = {
            "base_url": "https://example.test/proposals",
            "routes": [route("root", "/", "index.html")],
        }
        with mock.patch.object(validator, "read_public", side_effect=OSError("network down")):
            failures = validator.validate_public(registry, retries=0, wait_seconds=0, timeout=1)

        self.assertEqual(
            failures,
            ["root public https://example.test/proposals/: network down"],
        )

    def test_public_validation_retries_until_route_passes(self) -> None:
        registry = {
            "base_url": "https://example.test/proposals",
            "routes": [route("root", "/", "index.html")],
        }
        with (
            mock.patch.object(validator, "read_public", side_effect=[OSError("not yet"), "expected"]),
            mock.patch.object(validator.time, "sleep") as sleep,
        ):
            failures = validator.validate_public(registry, retries=1, wait_seconds=3, timeout=1)

        self.assertEqual(failures, [])
        sleep.assert_called_once_with(3)

    def test_load_registry_requires_top_level_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            registry = Path(tmp_dir) / "proposal-link-registry.json"
            registry.write_text("[]", encoding="utf-8")
            with mock.patch.object(validator, "REGISTRY", registry):
                with self.assertRaisesRegex(ValueError, "top-level JSON value must be an object"):
                    validator.load_registry()

    def test_main_reports_invalid_json_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            registry = Path(tmp_dir) / "proposal-link-registry.json"
            registry.write_text("{not json", encoding="utf-8")
            with (
                mock.patch.object(validator, "REGISTRY", registry),
                mock.patch("sys.argv", ["validate_proposal_routes.py", "--local"]),
                mock.patch("sys.stderr"),
            ):
                exit_code = validator.main()

        self.assertEqual(exit_code, 1)

    def test_main_fails_shape_errors_before_local_file_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            registry = Path(tmp_dir) / "proposal-link-registry.json"
            registry.write_text(
                json.dumps(
                    {
                        "base_url": "https://example.test/proposals",
                        "routes": [
                            {
                                "id": "broken-route",
                                "path": "/broken-route/",
                                "owner": "test-owner",
                                "purpose": "missing local file should be a shape error",
                                "locked": True,
                                "expected_markers": ["expected"],
                                "forbidden_markers": ["forbidden"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.object(validator, "REGISTRY", registry),
                mock.patch("sys.argv", ["validate_proposal_routes.py", "--local"]),
                mock.patch("sys.stderr"),
            ):
                exit_code = validator.main()

        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
