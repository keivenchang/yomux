# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from tests.helpers.fixture_tools import fixture_executable
import os
from pathlib import Path
import subprocess

import pytest

from tools.instance_isolation import normalize_yomux_environment
from tools import instance_isolation
from tools.system_status_latency_probe import config_dir_from_process


ROOT = Path(__file__).resolve().parents[1]
SHARED = Path.home() / 'dev/ai-config/agents/skills/yomux-dev-start/scripts/environment.sh'


@pytest.mark.parametrize('values,expected,warns', [
    ({'YOLOMUX_FIXTURE': 'old'}, 'old', True),
    ({'YOMUX_FIXTURE': 'new'}, 'new', False),
    ({'YOLOMUX_FIXTURE': 'same', 'YOMUX_FIXTURE': 'same'}, 'same', True),
    ({'YOLOMUX_FIXTURE': 'old', 'YOMUX_FIXTURE': 'new'}, 'new', True),
    ({'YOLOMUX_FIXTURE': 'old', 'YOMUX_FIXTURE': ''}, '', True),
])
def test_normalization_preserves_presence_and_warning_provenance(values, expected, warns, capsys, monkeypatch):
    monkeypatch.setattr(instance_isolation, '_legacy_alias_warning_emitted', False)
    normalized = normalize_yomux_environment(values, warn=True)
    assert normalized['YOMUX_FIXTURE'] == expected
    assert normalized['YOLOMUX_FIXTURE'] == expected
    first = capsys.readouterr().err
    assert bool(first) == warns
    assert normalized == normalize_yomux_environment(normalized, warn=True)
    assert not capsys.readouterr().err
    assert normalized.get('YOMUX_ENVIRONMENT_NORMALIZED') == '1'


@pytest.mark.parametrize('adapter,function', [
    (ROOT / 'tools/startup_common.sh', 'yomux_normalize_environment'),
    (SHARED, 'normalize_yomux_environment'),
])
def test_shell_adapter_and_child_do_not_warn_for_generated_aliases(adapter, function):
    if not adapter.exists():
        pytest.skip('external launcher adapter not installed')
    env = {'PATH': '/usr/bin:/bin', 'HOME': '/tmp', 'YOMUX_FIXTURE': 'new'}
    script = 'source "$1"; "$2"; "$2"; bash -c \'source "$1"; "$2"\' child "$1" "$2"'
    result = subprocess.run([fixture_executable("bash"), '-c', script, 'probe', str(adapter), function], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ''


def test_installed_tls_helper_empty_canonical_uses_default(tmp_path):
    helper = Path.home() / '.local/bin/yomux-tls-setup.sh'
    if not helper.exists():
        pytest.skip('operator helper not installed')
    env = {'PATH': os.environ['PATH'], 'HOME': str(tmp_path), 'YOMUX_CA_DIR': '', 'YOLOMUX_CA_DIR': '/invalid-legacy', 'YOMUX_STATE_DIR': '', 'YOLOMUX_STATE_DIR': '/invalid-state'}
    result = subprocess.run([str(helper), '--dry-run', '--no-trust'], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert str(tmp_path / '.local/share/yomux-ca/rootCA.crt') in result.stdout
    assert str(tmp_path / '.local/state/yomux/tls') in result.stdout
    assert '/invalid-' not in result.stdout
    assert 'leaf ->    /tls' not in result.stdout


def test_listener_configuration_resolves_canonical_process_mapping(tmp_path):
    assert config_dir_from_process({'YOMUX_CONFIG_DIR': str(tmp_path)}) == tmp_path
    assert config_dir_from_process({'YOMUX_CONFIG_DIR': str(tmp_path), 'YOLOMUX_CONFIG_DIR': '/invalid-legacy'}) == tmp_path


def test_normalization_warns_for_new_or_changed_legacy_handoff(capsys):
    env = normalize_yomux_environment({'YOMUX_FIXTURE': 'original'})
    env['YOLOMUX_SECOND'] = 'private-handoff-value'
    env = normalize_yomux_environment(env, warn=True)
    assert capsys.readouterr().err == 'WARNING: YOLOMUX_* environment names are deprecated; use YOMUX_*.\n'
    env['YOLOMUX_FIXTURE'] = 'changed-legacy'
    env = normalize_yomux_environment(env, warn=True)
    assert capsys.readouterr().err == 'WARNING: YOLOMUX_* environment names are deprecated; use YOMUX_*.\n'
    assert env['YOMUX_FIXTURE'] == 'original'
    env['YOMUX_FIXTURE'] = 'changed-canonical'
    normalize_yomux_environment(env, warn=True)
    assert capsys.readouterr().err == ''


def test_silent_normalization_retains_raw_legacy_warning(capsys):
    env = normalize_yomux_environment({'YOLOMUX_FIXTURE': 'private-value'})
    normalize_yomux_environment(env, warn=True)
    assert capsys.readouterr().err == 'WARNING: YOLOMUX_* environment names are deprecated; use YOMUX_*.\n'


def test_internal_assignment_records_only_published_aliases(capsys):
    env = {'YOMUX_FIXTURE': 'same'}
    instance_isolation.apply_yomux_environment_assignments(env, {'YOMUX_INTERNAL': 'value'})
    assert 'YOLOMUX_FIXTURE' not in env
    assert 'YOLOMUX_FIXTURE=' not in env['YOMUX_ENVIRONMENT_ALIASES']
    env['YOLOMUX_FIXTURE'] = 'same'
    normalize_yomux_environment(env, warn=True)
    assert capsys.readouterr().err == 'WARNING: YOLOMUX_* environment names are deprecated; use YOMUX_*.\n'


def test_clearing_assignment_retires_its_alias_provenance(capsys):
    env = normalize_yomux_environment({'YOMUX_FIXTURE': 'same'})
    instance_isolation.clear_yomux_environment_assignments(env, 'YOMUX_FIXTURE')
    assert 'YOLOMUX_FIXTURE=' not in env['YOMUX_ENVIRONMENT_ALIASES']
    env['YOLOMUX_FIXTURE'] = 'same'
    normalize_yomux_environment(env, warn=True)
    assert capsys.readouterr().err == 'WARNING: YOLOMUX_* environment names are deprecated; use YOMUX_*.\n'


def test_shell_handoff_preserves_python_alias_provenance():
    env = normalize_yomux_environment({'PATH': '/usr/bin:/bin', 'YOMUX_FIXTURE': 'original'})
    script = 'source "$1"; yomux_normalize_environment; export YOLOMUX_SECOND=private-value; yomux_normalize_environment; export YOMUX_FIXTURE=changed-canonical; yomux_normalize_environment; export YOLOMUX_FIXTURE=changed-legacy; yomux_normalize_environment'
    result = subprocess.run([fixture_executable("bash"), '-c', script, 'probe', str(ROOT / 'tools/product_environment.sh')], env=env, text=True, capture_output=True, check=True)
    assert result.stderr == 'WARNING: YOLOMUX_* environment names are deprecated; use YOMUX_*.\n' * 2
