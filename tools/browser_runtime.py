#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Resolve installed browser tools offline for gate admission and browser fixtures."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
from typing import Mapping, MutableMapping


CHROME_COMMANDS = ('chrome', 'google-chrome', 'chromium', 'chromium-browser', 'google-chrome-stable')
MACOS_CHROME_PATHS = (
    Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),
    Path('/Applications/Chromium.app/Contents/MacOS/Chromium'),
)
SELENIUM_CACHE_PLATFORMS = {
    ('Darwin','arm64'):'mac-arm64', ('Darwin','aarch64'):'mac-arm64',
    ('Darwin','x86_64'):'mac-x64', ('Darwin','amd64'):'mac-x64',
    ('Linux','x86_64'):'linux64', ('Linux','amd64'):'linux64',
}



class BrowserUnavailable(RuntimeError):
    def __init__(self, component: str, message: str, *, configured: bool = False):
        self.component = component
        self.configured = configured
        super().__init__(message)


@dataclass(frozen=True)
class BrowserExecutables:
    chrome: str
    chromedriver: str

    def environment(self) -> dict[str, str]:
        return {'CHROME_BIN':self.chrome, 'CHROMEDRIVER_BIN':self.chromedriver}


def _executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _configured_executable(name: str, environ: Mapping[str, str]) -> str | None:
    configured = environ.get(name, '').strip()
    if not configured:
        return None
    path = Path(configured)
    found = str(path.absolute()) if _executable(path) else shutil.which(configured, path=environ.get('PATH', ''))
    if not found or not _executable(Path(found)):
        component = 'browser' if name == 'CHROME_BIN' else 'driver'
        raise BrowserUnavailable(component, f'{name} is not an executable file: {configured}', configured=True)
    return found


def resolve_chrome(environ: Mapping[str, str] | None = None, *, system: str | None = None) -> str:
    values = os.environ if environ is None else environ
    configured = _configured_executable('CHROME_BIN', values)
    if configured:
        return configured
    for name in CHROME_COMMANDS:
        candidate = shutil.which(name, path=values.get('PATH', ''))
        if candidate:
            return candidate
    if (system or platform.system()) == 'Darwin':
        for path in MACOS_CHROME_PATHS:
            if _executable(path):
                # Keep signed app tools at their installed paths, including any original aliases.
                return str(path)
    raise BrowserUnavailable('browser', 'Chrome or Chromium executable is unavailable')


def _version_parts(value: str) -> tuple[int, ...]:
    return tuple(map(int, value.split('.'))) if re.fullmatch(r'\d+(?:\.\d+)+', value) else ()


def _browser_major(chrome: str) -> int:
    try:
        result = subprocess.run([chrome, '--version'], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BrowserUnavailable('driver', f'cannot determine browser version for cached chromedriver: {error}') from error
    version = re.search(r'\b(\d+)\.\d+\.\d+(?:\.\d+)?\b', result.stdout)
    if result.returncode or version is None:
        raise BrowserUnavailable('driver', f'cannot determine browser version for cached chromedriver: {chrome}')
    return int(version.group(1))


def resolve_browser_executables(environ: Mapping[str, str] | None = None, *, system: str | None = None,
                                machine: str | None = None) -> BrowserExecutables:
    values = os.environ if environ is None else environ
    chrome = resolve_chrome(values, system=system)
    driver = _configured_executable('CHROMEDRIVER_BIN', values)
    if driver is None:
        driver = shutil.which('chromedriver', path=values.get('PATH', ''))
    if driver:
        return BrowserExecutables(chrome, driver)
    current_system = system or platform.system()
    current_machine = machine or platform.machine()
    cache_platform = SELENIUM_CACHE_PLATFORMS.get((current_system, current_machine.casefold()))
    if cache_platform is None:
        raise BrowserUnavailable('driver', f'no offline Selenium driver cache is supported for {current_system}/{current_machine}; configure an installed chromedriver')
    if not values.get('HOME'):
        raise BrowserUnavailable('driver', 'HOME is not declared; configure an installed chromedriver instead of an ambient cache')
    home = Path(values['HOME'])
    cache = home / '.cache' / 'selenium' / 'chromedriver' / cache_platform
    candidates = [(parts, path) for path in cache.glob('*/chromedriver')
                  if _executable(path) and (parts := _version_parts(path.parent.name))]
    if candidates:
        major = _browser_major(chrome)
        matching = [(version, path) for version, path in candidates if version[0] == major]
        if matching:
            return BrowserExecutables(chrome, str(max(matching, key=lambda row: row[0])[1]))
        raise BrowserUnavailable('driver', f'cached chromedriver does not match browser major {major}; no download was attempted')
    raise BrowserUnavailable('driver', 'chromedriver is unavailable on PATH and in the private Selenium cache; no download was attempted')


def admit_browser_environment(environ: MutableMapping[str, str], *,
                              browser_tools: BrowserExecutables | None = None) -> BrowserExecutables | None:
    """Carry admitted executable paths across HOME isolation without retaining its source HOME.

    Missing tools leave nonbrowser pytest usable. Requested browser gates and fixtures retain their
    strict resolver call, so an unavailable or invalid explicit configuration still fails there.
    """
    if browser_tools is None:
        try:
            browser_tools = resolve_browser_executables(environ)
        except BrowserUnavailable:
            return None
    environ.update(browser_tools.environment())
    return browser_tools


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--format', choices=('json', 'lines'), default='json')
    arguments = parser.parse_args()
    try:
        tools = resolve_browser_executables()
    except BrowserUnavailable as error:
        parser.exit(2 if error.configured else 1, f'{error}\n')
    if arguments.format == 'lines':
        if any('\n' in path for path in (tools.chrome, tools.chromedriver)):
            parser.exit(2, 'browser executable paths must not contain newlines\n')
        print(tools.chrome)
        print(tools.chromedriver)
    else:
        print(json.dumps({'chrome': tools.chrome, 'chromedriver': tools.chromedriver}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
