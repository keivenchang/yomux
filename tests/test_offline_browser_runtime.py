# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Browser admission and execution resolve the same offline tools without host leaks."""

import json
from pathlib import Path
import platform
import subprocess

import pytest

from tests.helpers.fixture_tools import fixture_command, fixture_executable, fixture_script
from tools import browser_runtime
from tools.browser_runtime import BrowserExecutables, BrowserUnavailable, resolve_browser_executables


def executable(path, version='157.0.8103.79'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fixture_script(f'#!/bin/sh\nprintf "Google Chrome {version}\\n"\n'), encoding='utf-8')
    path.chmod(0o755)
    return path


def cached_driver(home, version, cache_platform='mac-arm64'):
    return executable(home / '.cache/selenium/chromedriver' / cache_platform / version / 'chromedriver', version)


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_runtime, 'MACOS_CHROME_PATHS', ())
    binary_dir = tmp_path / 'bin'
    binary_dir.mkdir()
    return {'HOME':str(tmp_path / 'private-home'),'PATH':str(binary_dir)}


@pytest.mark.parametrize('command', browser_runtime.CHROME_COMMANDS)
def test_linux_path_browser_choices_share_explicit_offline_driver(environment, command):
    chrome = executable(Path(environment['PATH']) / command)
    driver = executable(Path(environment['PATH']) / 'chromedriver')
    assert resolve_browser_executables(environment, system='Linux') == BrowserExecutables(str(chrome),str(driver))


def test_macos_installed_bundle_and_cached_driver_resolve_without_overrides(environment, tmp_path, monkeypatch):
    chrome = executable(tmp_path / 'Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    driver = cached_driver(Path(environment['HOME']), '157.0.8103.78')
    monkeypatch.setattr(browser_runtime, 'MACOS_CHROME_PATHS', (chrome,))
    assert resolve_browser_executables(environment, system='Darwin', machine='arm64') == BrowserExecutables(str(chrome),str(driver))


@pytest.mark.parametrize('setting', ['CHROME_BIN','CHROMEDRIVER_BIN'])
@pytest.mark.parametrize('invalid_kind', ['missing','not-executable','directory'])
def test_explicit_bad_override_does_not_fall_back_to_installed_tools(environment, tmp_path, setting, invalid_kind):
    executable(Path(environment['PATH']) / 'chromium')
    executable(Path(environment['PATH']) / 'chromedriver')
    invalid = tmp_path / 'invalid-tool'
    if invalid_kind == 'directory':
        invalid.mkdir()
    elif invalid_kind == 'not-executable':
        invalid.write_bytes(b'not executable')
    with pytest.raises(BrowserUnavailable, match=setting):
        resolve_browser_executables({**environment,setting:str(invalid)}, system='Linux')


def test_explicit_bare_commands_resolve_on_declared_path(environment):
    chrome = executable(Path(environment['PATH']) / 'my-chrome')
    driver = executable(Path(environment['PATH']) / 'my-driver')
    result = resolve_browser_executables({**environment,'CHROME_BIN':'my-chrome','CHROMEDRIVER_BIN':'my-driver'})
    assert result == BrowserExecutables(str(chrome),str(driver))


def test_cached_driver_matches_browser_major_and_numeric_version(environment):
    chrome = executable(Path(environment['PATH']) / 'chromium', '157.0.8103.79')
    home = Path(environment['HOME'])
    cached_driver(home, '158.0.1.1')
    cached_driver(home, '157.9.1.1')
    driver = cached_driver(home, '157.10.1.1')
    cached_driver(home, '157.99.1.1', 'mac-x64')
    result = resolve_browser_executables(environment, system='Darwin', machine='arm64')
    assert result == BrowserExecutables(str(chrome),str(driver))


def test_cached_driver_with_wrong_browser_major_is_unavailable(environment):
    executable(Path(environment['PATH']) / 'chromium', '157.0.8103.79')
    cached_driver(Path(environment['HOME']), '156.0.1.1')
    with pytest.raises(BrowserUnavailable, match='does not match browser major 157; no download'):
        resolve_browser_executables(environment, system='Darwin', machine='arm64')


def test_private_home_does_not_use_operator_driver_cache(environment, tmp_path, monkeypatch):
    executable(Path(environment['PATH']) / 'chromium')
    operator_home = tmp_path / 'operator-home'
    cached_driver(operator_home, '157.0.8103.78')
    monkeypatch.setattr(browser_runtime.Path, 'home', lambda: operator_home)
    with pytest.raises(BrowserUnavailable, match='private Selenium cache; no download'):
        resolve_browser_executables(environment, system='Darwin', machine='arm64')


def test_linux_offline_cache_uses_installed_original_executables(environment):
    chrome = executable(Path(environment['PATH']) / 'chromium')
    driver = cached_driver(Path(environment['HOME']), '157.0.8103.78', 'linux64')
    assert resolve_browser_executables(environment, system='Linux', machine='x86_64') == BrowserExecutables(str(chrome),str(driver))


def test_shell_resolver_and_cli_use_the_same_private_offline_cache(environment, tmp_path):
    binary_dir = Path(environment['PATH'])
    python = binary_dir / 'python3'
    python.write_text(fixture_script(f'#!/bin/sh\nexec {fixture_command("python3")} "$@"\n'), encoding='utf-8')
    python.chmod(0o755)
    chrome = executable(tmp_path / 'Installed Chrome.app/Contents/MacOS/Google Chrome')
    cache_platform = browser_runtime.SELENIUM_CACHE_PLATFORMS.get((platform.system(),platform.machine().casefold()))
    if cache_platform is None:
        pytest.skip('this host has no supported Selenium driver cache platform')
    driver = cached_driver(Path(environment['HOME']), '157.0.8103.78', cache_platform)
    values = {**environment,'CHROME_BIN':str(chrome)}
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([fixture_executable('bash'),'-c',
        'source "$1"; bazel_native_browser_resolve; printf "%s|%s" "$BAZEL_NATIVE_CHROME_BIN" "$BAZEL_NATIVE_CHROMEDRIVER_BIN"',
        'offline-browser-resolver',str(root/'tools/bazel_test_common.sh')],env=values,capture_output=True,text=True,check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout == f'{chrome}|{driver}'
    cli = subprocess.run([fixture_executable('python3'),str(root/'tools/browser_runtime.py')],
                         env=values,capture_output=True,text=True,check=False)
    assert cli.returncode == 0, cli.stderr
    assert json.loads(cli.stdout) == {'chrome':str(chrome),'chromedriver':str(driver)}


@pytest.mark.parametrize('status,expected', [(0,False),(1,True),(2,False)])
def test_native_runner_only_omits_browser_targets_for_true_absence(status, expected):
    root = Path(__file__).resolve().parents[1]
    source = (root / 'tools/run_bazel_tests.sh').read_text()
    start = source.index('  if bazel_native_browser_resolve; then')
    stop = source.index('\nfi\n', start)
    script = f'''set -e
native_browser_args=()
native_test_filter_args=()
BAZEL_NATIVE_CHROME_BIN=/fixture/chrome
BAZEL_NATIVE_CHROMEDRIVER_BIN=/fixture/chromedriver
bazel_native_browser_resolve() {{ return {status}; }}
''' + source[start:stop] + '\nprintf "FILTERS:%s" "${native_test_filter_args[*]}"\n'
    result = subprocess.run([fixture_executable('bash'),'-c',script],capture_output=True,text=True,check=False)
    assert result.returncode == (2 if status == 2 else 0), result.stderr
    assert ('--test_tag_filters=-requires_chrome' in result.stdout) == expected
    assert ('skipped_targets' in result.stdout) == expected


def test_cli_preserves_invalid_explicit_override_failure_status(environment, tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([fixture_executable('python3'),str(root/'tools/browser_runtime.py')],
        env={**environment,'CHROME_BIN':str(tmp_path/'absent-chrome')},capture_output=True,text=True,check=False)
    assert result.returncode == 2
    assert 'CHROME_BIN is not an executable file' in result.stderr
    assert result.stdout == ''


@pytest.mark.parametrize('machine', ['arm64','aarch64'])
def test_linux_arm_refuses_x86_cached_driver_but_keeps_declared_path_driver(environment, machine):
    chrome = executable(Path(environment['PATH']) / 'chromium')
    cached_driver(Path(environment['HOME']), '157.0.8103.78', 'linux64')
    with pytest.raises(BrowserUnavailable, match='no offline Selenium driver cache is supported'):
        resolve_browser_executables(environment, system='Linux', machine=machine)
    driver = executable(Path(environment['PATH']) / 'chromedriver')
    assert resolve_browser_executables(environment, system='Linux', machine=machine) == BrowserExecutables(str(chrome),str(driver))


def test_admission_carries_only_exact_executables_across_private_home(environment, monkeypatch):
    monkeypatch.setattr(browser_runtime.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(browser_runtime.platform, 'machine', lambda: 'arm64')
    chrome = executable(Path(environment['PATH']) / 'chromium')
    driver = cached_driver(Path(environment['HOME']), '157.0.8103.78')
    original_home = environment['HOME']
    admitted = browser_runtime.admit_browser_environment(environment)
    assert admitted == BrowserExecutables(str(chrome),str(driver))
    environment['HOME'] = str(Path(original_home).with_name('isolated-home'))
    monkeypatch.setattr(browser_runtime, '_browser_major', lambda _chrome: pytest.fail('replacement queried an operator cache'))
    assert resolve_browser_executables(environment) == admitted
    assert environment == {'HOME':environment['HOME'],'PATH':environment['PATH'],
                           'CHROME_BIN':str(chrome),'CHROMEDRIVER_BIN':str(driver)}


def test_optional_admission_does_not_hide_strict_fixture_configuration_errors(environment):
    environment['CHROME_BIN'] = '/fixture/missing-chrome'
    before = dict(environment)
    assert browser_runtime.admit_browser_environment(environment) is None
    assert environment == before
    with pytest.raises(BrowserUnavailable, match='CHROME_BIN is not an executable file'):
        resolve_browser_executables(environment)


@pytest.mark.parametrize('home', [None, ''])
def test_undeclared_home_never_uses_ambient_driver_cache(environment, tmp_path, monkeypatch, home):
    chrome = executable(Path(environment['PATH']) / 'chromium')
    cached_driver(tmp_path / 'operator-home', '157.0.8103.78')
    values = dict(environment)
    if home is None:
        values.pop('HOME')
    else:
        values['HOME'] = home
    monkeypatch.setattr(browser_runtime.Path, 'home', lambda: pytest.fail('undeclared HOME consulted the operator account'))
    with pytest.raises(BrowserUnavailable, match='HOME is not declared'):
        resolve_browser_executables(values, system='Darwin', machine='arm64')
    driver = executable(Path(environment['PATH']) / 'chromedriver')
    assert resolve_browser_executables(values) == BrowserExecutables(str(chrome),str(driver))


@pytest.mark.parametrize('newline_tool', ['chrome','chromedriver'])
def test_cli_unrepresentable_line_paths_are_configuration_errors(environment, tmp_path, newline_tool):
    chrome = executable(tmp_path / ('chrome\nname' if newline_tool == 'chrome' else 'chrome'))
    driver = executable(tmp_path / ('driver\nname' if newline_tool == 'chromedriver' else 'driver'))
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([fixture_executable('python3'),str(root/'tools/browser_runtime.py'),'--format','lines'],
        env={**environment,'CHROME_BIN':str(chrome),'CHROMEDRIVER_BIN':str(driver)},
        capture_output=True,text=True,check=False)
    assert result.returncode == 2
    assert 'must not contain newlines' in result.stderr
    assert result.stdout == ''
