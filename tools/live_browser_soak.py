#!/usr/bin/env python3
"""Run the operator-authorized local HTTPS browser soak gate."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.common.exceptions import WebDriverException

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from yolomux_lib.live_browser_soak import evidence_failed
from yolomux_lib.live_browser_soak import record_failure
from yolomux_lib.live_browser_soak import run_soak
from yolomux_lib.live_browser_soak import terminal_failure
from yolomux_lib.live_browser_soak import validate_arguments
from yolomux_lib.live_browser_soak import validate_clean_soak_prerequisite
from yolomux_lib.live_browser_soak import validate_success_artifact
from yolomux_lib.live_browser_soak import write_artifact


CLEANUP_TIMEOUT_SECONDS = 15


def cleanup_driver(driver: object, timeout_seconds: float = CLEANUP_TIMEOUT_SECONDS) -> dict[str, str] | None:
    """Bound cleanup to this WebDriver and its service process only."""
    result: list[WebDriverException] = []

    def quit_driver() -> None:
        try:
            driver.quit()
        except WebDriverException as error:
            result.append(error)

    worker = threading.Thread(target=quit_driver, name="live-browser-soak-cleanup", daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        service = driver.service
        process = service.process
        if process is not None:
            process.terminate()
            message = "driver quit exceeded cleanup deadline; terminated its WebDriver service process"
        else:
            message = "driver quit exceeded cleanup deadline; WebDriver service process was unavailable"
        return {"phase": "cleanup", "terminal": "WebDriverCleanupTimeout", "message": message}
    if result:
        return terminal_failure("cleanup", result[0])
    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--duration", type=int, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--expected-bundle-sha256", required=True)
    parser.add_argument("--expected-cwd", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--negative-browser-error-probe", action="store_true")
    parser.add_argument("--clean-soak-artifact", type=Path, help="clean 600s soak artifact proving the full journey on this exact identity; required by --negative-browser-error-probe")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    artifact: dict[str, object] = {"url": args.url, "expected_identity": {"cwd": args.expected_cwd, "head": args.expected_head, "bundle_sha256": args.expected_bundle_sha256}, "samples": []}
    driver = None
    phase = "preflight"
    try:
        validate_arguments(args.url, args.duration, args.expected_head, args.expected_bundle_sha256, args.output, args.expected_cwd, args.negative_browser_error_probe)
        clean_soak_prerequisite = None
        if args.negative_browser_error_probe:
            if args.clean_soak_artifact is None:
                raise ValueError("--negative-browser-error-probe requires --clean-soak-artifact")
            clean_soak_prerequisite = validate_clean_soak_prerequisite(
                args.clean_soak_artifact,
                url=args.url,
                expected_head=args.expected_head,
                expected_bundle_sha256=args.expected_bundle_sha256,
                expected_cwd=args.expected_cwd,
            )
        elif args.clean_soak_artifact is not None:
            raise ValueError("--clean-soak-artifact only applies to --negative-browser-error-probe")
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--ignore-certificate-errors")
        options.set_capability("acceptInsecureCerts", True)
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(30)
        driver.set_script_timeout(30)
        phase = "runtime"
        artifact = run_soak(driver, url=args.url, duration=args.duration, expected_head=args.expected_head, expected_bundle_sha256=args.expected_bundle_sha256, expected_cwd=args.expected_cwd, negative_probe=args.negative_browser_error_probe, clean_soak_prerequisite=clean_soak_prerequisite)
    except (AssertionError, OSError, RuntimeError, subprocess.SubprocessError, TimeoutException, ValueError, WebDriverException, json.JSONDecodeError) as error:
        record_failure(artifact, terminal_failure(phase, error))
    finally:
        if driver is not None:
            try:
                cleanup_error = cleanup_driver(driver)
            except (AttributeError, OSError, RuntimeError, WebDriverException) as error:
                cleanup_error = terminal_failure("cleanup", error)
            if cleanup_error is not None:
                artifact["cleanup_failure"] = cleanup_error
                record_failure(artifact, cleanup_error)
        if not evidence_failed(artifact):
            try:
                validate_success_artifact(artifact)
            except RuntimeError as error:
                record_failure(artifact, terminal_failure("runtime", error))
        try:
            write_artifact(args.output, artifact)
        except FileExistsError:
            return 1
    return 1 if evidence_failed(artifact) else 0


if __name__ == "__main__":
    raise SystemExit(main())
