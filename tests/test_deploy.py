"""Exercise deployment without installing packages or changing a live service."""

import os
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest

import deploy


def test_help_works_without_root():
    result = subprocess.run([sys.executable, deploy.__file__, '--help'],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert 'SIMPLE_TODO_DATA_DIR' in result.stdout
    assert '--no-restart' in result.stdout


def test_non_root_deployment_stops_before_writing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(deploy.os, 'geteuid', lambda: 1000)
    target = tmp_path / 'deployment'
    assert deploy.main([str(target)]) == 1
    assert not target.exists()
    assert 'run as root' in capsys.readouterr().err


def test_paths_use_machine_defaults_and_allow_overrides(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('SIMPLE_TODO_DATA_DIR', raising=False)
    source = tmp_path / 'source'
    default = deploy.deployment_paths(source=source)
    assert default.target == Path('/opt/simple_todo').resolve()
    assert default.state == Path('/var/lib/simple_todo')
    assert default.external_data
    assert deploy.deployment_paths('app', source=source).target == tmp_path / 'app'
    monkeypatch.setenv('SIMPLE_TODO_DATA_DIR', 'user data')
    external = deploy.deployment_paths('app', source=source)
    assert external.state == tmp_path / 'user data'
    assert external.external_data
    assert deploy.deployment_paths('app', 'explicit state', source=source).state == tmp_path / 'explicit state'


def test_existing_data_directory_survives_neutral_default_change(tmp_path, monkeypatch):
    monkeypatch.delenv('SIMPLE_TODO_DATA_DIR', raising=False)
    target, state, source = tmp_path / 'installed', tmp_path / 'existing state 10%', tmp_path / 'source'
    target.mkdir()
    (target / deploy.SERVICE_FILE).write_text(deploy.render_service(deploy.Deployment(source, target, state, True)))
    assert deploy.deployment_paths(target, source=source).state == state
    monkeypatch.setenv('SIMPLE_TODO_DATA_DIR', str(tmp_path / 'override'))
    assert deploy.deployment_paths(target, source=source).state == tmp_path / 'override'
    assert deploy.deployment_paths(target, tmp_path / 'explicit', source=source).state == tmp_path / 'explicit'


@pytest.mark.parametrize('existing_override', [False, True])
def test_upgrade_preserves_private_wiki_settings_without_executing_config(tmp_path, existing_override):
    source, target, state = (tmp_path / name for name in ('source', 'installed', 'state'))
    for path in (source, target, state):
        path.mkdir()
    (source / 'config.py').write_text('ENABLE_DOKUWIKI_LINKS = False\nDOKUWIKI_LINK_FSTRING = ""\nDOKUWIKI_ITEM_LINK_FSTRING = ""\n')
    (target / 'config.py').write_text('ENABLE_DOKUWIKI_LINKS = True\nDOKUWIKI_LINK_FSTRING = "https://wiki.example.test/old"\n'
                                    'DOKUWIKI_ITEM_LINK_FSTRING = "https://wiki.example.test/items/{item_id}"\n'
                                    'raise RuntimeError("Installed code must never execute during migration")\n')
    path = state / 'settings.json'
    settings = {'SHOW_QUICK_ADD_FORMS': True}
    if existing_override:
        settings['DOKUWIKI_LINK_FSTRING'] = 'https://wiki.example.test/custom'
        path.write_text(json.dumps(settings))
    deployment = deploy.Deployment(source, target, state, True)
    deploy.preserve_wiki_settings(deployment)
    saved = path.read_bytes()
    migrated = json.loads(saved)
    assert migrated['ENABLE_DOKUWIKI_LINKS'] is True
    assert migrated['DOKUWIKI_LINK_FSTRING'].endswith('/custom' if existing_override else '/old')
    assert migrated['DOKUWIKI_ITEM_LINK_FSTRING'] == 'https://wiki.example.test/items/{item_id}'
    if existing_override:
        assert migrated['SHOW_QUICK_ADD_FORMS'] is True
    assert path.stat().st_mode & 0o777 == 0o600
    deploy.preserve_wiki_settings(deployment)
    assert path.read_bytes() == saved


def test_invalid_site_settings_stop_migration_without_overwriting_them(tmp_path):
    source, target, state = (tmp_path / name for name in ('source', 'installed', 'state'))
    for path in (source, target, state):
        path.mkdir()
    (source / 'config.py').write_text('ENABLE_DOKUWIKI_LINKS = False\n')
    (target / 'config.py').write_text('ENABLE_DOKUWIKI_LINKS = True\n')
    path = state / 'settings.json'
    path.write_text('[]\n')
    with pytest.raises(ValueError, match='JSON object'):
        deploy.preserve_wiki_settings(deploy.Deployment(source, target, state, True))
    assert path.read_text() == '[]\n'


@pytest.mark.parametrize('state', ['source/state', 'app/state', 'app'])
def test_external_state_cannot_be_inside_code_directories(tmp_path, state):
    with pytest.raises(ValueError, match='DATA_DIRECTORY must be outside'):
        deploy.deployment_paths(tmp_path / 'app', tmp_path / state, source=tmp_path / 'source')


@pytest.mark.parametrize('target', ['source', 'source/app', '.'])
def test_source_and_target_cannot_overlap(tmp_path, target):
    with pytest.raises(ValueError, match='must not overlap'):
        deploy.deployment_paths(tmp_path / target, source=tmp_path / 'source')


@pytest.mark.parametrize('existing', [False, True])
def test_environment_merge_preserves_secrets_and_external_paths(tmp_path, existing, capsys):
    source, state = tmp_path / 'source', tmp_path / 'state'
    source.mkdir()
    state.mkdir()
    (source / '.env').write_text(
        '# Local configuration\nSIMPLE_TODO_PORT=3102\n'
        'SIMPLE_TODO_DB_PATH=local.db\nSIMPLE_TODO_DATA_DIR=/local/state\n'
        'NEW_KEY="a value with spaces = and $symbols"\n'
        'SIMPLE_TODO_SESSION_SECRET=source-secret\n'
    )
    environment = state / '.env'
    if existing:
        # No trailing newline must not join the next key onto this secret.
        environment.write_text('SIMPLE_TODO_PORT=43123\nSIMPLE_TODO_SESSION_SECRET=server-secret')
    deploy.merge_environment(source, state, external_data=True)
    content = environment.read_text()
    assert 'SIMPLE_TODO_DB_PATH=' not in content
    assert 'SIMPLE_TODO_DATA_DIR=' not in content
    assert 'NEW_KEY="a value with spaces = and $symbols"\n' in content
    assert f'SIMPLE_TODO_PORT={43123 if existing else 3102}\n' in content
    assert f'SIMPLE_TODO_SESSION_SECRET={"server-secret" if existing else "source-secret"}' in content
    assert content.count('SIMPLE_TODO_SESSION_SECRET=') == 1
    assert environment.stat().st_mode & 0o777 == 0o600
    deploy.merge_environment(source, state, external_data=True)
    assert environment.read_text() == content
    output = capsys.readouterr().out
    assert 'server-secret' not in output and 'source-secret' not in output


def test_generated_session_secret_is_stable_and_legacy_keys_are_kept(tmp_path):
    source, state = tmp_path / 'source', tmp_path / 'state'
    source.mkdir()
    state.mkdir()
    (source / '.env').write_text('SIMPLE_TODO_DB_PATH=legacy.db\n')
    deploy.merge_environment(source, state)
    first = (state / '.env').read_text()
    assert 'SIMPLE_TODO_DB_PATH=legacy.db\n' in first
    assert re.search(r'^SIMPLE_TODO_SESSION_SECRET=[0-9a-f]{64}$', first, re.MULTILINE)
    deploy.merge_environment(source, state)
    assert (state / '.env').read_text() == first


def test_sync_preserves_deployment_state_and_removes_obsolete_code(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'app'
    source.mkdir()
    target.mkdir()
    (source / 'main.py').write_text('new code')
    (target / 'obsolete.py').write_text('old code')
    preserved = ['.env', '.htpasswd', 'user.db', 'user.db-wal', 'settings.json',
                 'session-secret', 'certs/server.key', 'backups/previous/main.py', 'server.log']
    for name in preserved:
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'keep {name}')
    deploy.sync_files(source, target)
    assert not (target / 'obsolete.py').exists()
    assert (target / 'main.py').read_text() == 'new code'
    assert all((target / name).read_text() == f'keep {name}' for name in preserved)


@pytest.mark.parametrize('restart', [True, False])
def test_deployment_workflow_restarts_only_after_files_and_permissions_are_ready(tmp_path, monkeypatch, restart):
    source, target, state = (tmp_path / name for name in ('source', 'app', 'state'))
    source.mkdir()
    (source / 'main.py').write_text('application')
    (source / 'requirements.txt').write_text('example-package\n')
    (source / '.env').write_text('SIMPLE_TODO_PORT=43123\n')
    deployment = deploy.Deployment(source, target, state, True)
    commands, ownership = [], []
    real_run = subprocess.run

    def run(command, **kwargs):
        commands.append(command)
        if command[0] == 'systemctl':
            assert kwargs['check'] is True
            assert (target / deploy.SERVICE_FILE).read_text() == deploy.render_service(deployment)
            assert ownership[-1][1] == {'recursive': True}
        if command[0] == 'rsync':
            return real_run(command, **kwargs)
        if command[1:3] == ['-m', 'venv']:
            (Path(command[-1]) / 'bin').mkdir(parents=True)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(deploy.subprocess, 'run', run)
    monkeypatch.setattr(deploy, 'change_owner', lambda *args, **kwargs: ownership.append((args, kwargs)))
    deploy.deploy(deployment, restart=restart)
    assert (target / 'main.py').read_text() == 'application'
    assert (target / deploy.SERVICE_FILE).read_text() == deploy.render_service(deployment)
    assert state.stat().st_mode & 0o777 == 0o700
    assert (state / '.env').stat().st_mode & 0o777 == 0o600
    assert [sys.executable, '-m', 'venv', str(target / '.venv')] in commands
    python = str(target / '.venv/bin/python')
    assert [python, '-m', 'pip', 'install', '--upgrade', 'pip'] in commands
    assert [python, '-m', 'pip', 'install', '-r', 'requirements.txt'] in commands
    service_commands = [command for command in commands if command[0] == 'systemctl']
    assert service_commands == ([['systemctl', 'restart', deploy.SERVICE_FILE]] if restart else [])
    if restart:
        assert commands[-1] == ['systemctl', 'restart', deploy.SERVICE_FILE]
    assert ownership[-1][1] == {'recursive': True}
    commands.clear()
    deploy.deploy(deployment, restart=restart)
    assert not any(command[1:3] == ['-m', 'venv'] for command in commands)


@pytest.mark.parametrize('failure', ['dependencies', 'restart'])
def test_deployment_failures_return_error_without_reporting_success(tmp_path, monkeypatch, capsys, failure):
    source, target, state = (tmp_path / name for name in ('source', 'app', 'state'))
    source.mkdir()
    (target / '.venv').mkdir(parents=True)
    deployment = deploy.Deployment(source, target, state, True)
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if failure == 'restart' and command[0] == 'systemctl':
            raise subprocess.CalledProcessError(1, command)
        if failure == 'dependencies' and command[1:3] == ['-m', 'pip']:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(deploy.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(deploy, 'deployment_paths', lambda *args: deployment)
    monkeypatch.setattr(deploy, 'sync_files', lambda *args: None)
    monkeypatch.setattr(deploy, 'change_owner', lambda *args, **kwargs: None)
    monkeypatch.setattr(deploy.subprocess, 'run', run)
    assert deploy.main([]) == 1
    if failure == 'dependencies':
        assert not any(command[0] == 'systemctl' for command in commands)
    output = capsys.readouterr()
    assert 'Deployment failed:' in output.err
    assert 'Deployment Complete!' not in output.out


def test_no_restart_flag_reaches_deployment(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy.os, 'geteuid', lambda: 0)
    calls = []
    monkeypatch.setattr(deploy, 'deploy', lambda deployment, **kwargs: calls.append(kwargs))
    assert deploy.main([str(tmp_path / 'app'), '--no-restart']) == 0
    assert calls == [{'restart': False}]


def test_ownership_does_not_follow_links_outside_deployment(tmp_path, monkeypatch):
    target, outside = tmp_path / 'app', tmp_path / 'private'
    target.mkdir()
    outside.mkdir()
    (outside / 'secret').write_text('private')
    (target / 'external').symlink_to(outside, target_is_directory=True)
    changed = []
    monkeypatch.setattr(deploy.os, 'chown', lambda path, uid, gid, **kwargs: changed.append((Path(path), kwargs)))
    deploy.change_owner(target, os.getuid(), os.getgid(), recursive=True)
    assert {path for path, _ in changed} == {target, target / 'external'}
    assert all(options == {'follow_symlinks': False} for _, options in changed)


def test_resolve_python_precedence(tmp_path, monkeypatch):
    custom_py = tmp_path / 'custom_python'
    custom_py.write_text('#!/bin/sh\n')
    custom_py.chmod(0o755)

    # 1. Explicit requested path
    assert deploy.resolve_python(str(custom_py)) == str(custom_py.resolve())

    # 2. PYTHON environment variable
    monkeypatch.setenv('PYTHON', str(custom_py))
    assert deploy.resolve_python() == str(custom_py.resolve())
    monkeypatch.delenv('PYTHON')

    # 3. Invalid path raises ValueError
    with pytest.raises(ValueError, match='not found'):
        deploy.resolve_python(str(tmp_path / 'nonexistent'))


def test_clear_venv_and_version_mismatch_triggers_clear(tmp_path, monkeypatch):
    source, target, state = (tmp_path / name for name in ('source', 'app', 'state'))
    source.mkdir()
    target.mkdir()
    venv = target / '.venv'
    venv.mkdir()
    cfg = venv / 'pyvenv.cfg'
    cfg.write_text('version = 3.13.1\n')

    commands = []
    def run(command, **kwargs):
        commands.append(command)
        if command[1:3] == ['-m', 'venv']:
            (Path(command[-1]) / 'bin').mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(deploy.subprocess, 'run', run)
    monkeypatch.setattr(deploy, 'python_version', lambda path: (3, 15))
    monkeypatch.setattr(deploy, 'sync_files', lambda *args: None)
    monkeypatch.setattr(deploy, 'merge_environment', lambda *args, **kwargs: None)
    monkeypatch.setattr(deploy, 'change_owner', lambda *args, **kwargs: None)

    deployment = deploy.Deployment(source, target, state, False, python_bin='/mock/python3.15')
    deploy.deploy(deployment, restart=False)
    assert ['/mock/python3.15', '-m', 'venv', '--clear', str(venv)] in commands

    commands.clear()
    # Now simulate cfg with matching version
    cfg.write_text('version = 3.15.2\n')
    deploy.deploy(deployment, restart=False)
    assert not any(cmd[1:3] == ['-m', 'venv'] for cmd in commands)

    # With clear_venv=True, it clears even when version matches
    commands.clear()
    deploy.deploy(deployment, restart=False, clear_venv=True)
    assert ['/mock/python3.15', '-m', 'venv', '--clear', str(venv)] in commands


def test_main_handles_python_and_clear_venv_flags(tmp_path, monkeypatch):
    custom_py = tmp_path / 'python3.15'
    custom_py.write_text('#!/bin/sh\n')
    custom_py.chmod(0o755)

    calls = []
    monkeypatch.setattr(deploy.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(deploy, 'python_version', lambda path: (3, 15))
    monkeypatch.setattr(deploy, 'deploy', lambda deployment, **kwargs: calls.append((deployment, kwargs)))

    assert deploy.main([str(tmp_path / 'app'), '--python', str(custom_py), '--clear-venv', '--no-restart']) == 0
    assert len(calls) == 1
    dep, kwargs = calls[0]
    assert dep.python_bin == str(custom_py.resolve())
    assert kwargs == {'restart': False, 'clear_venv': True}


def test_resolve_uv_precedence(tmp_path, monkeypatch):
    custom_uv = tmp_path / 'custom_uv'
    custom_uv.write_text('#!/bin/sh\n')
    custom_uv.chmod(0o755)

    assert deploy.resolve_uv(no_uv=True) is None
    assert deploy.resolve_uv(str(custom_uv)) == str(custom_uv.resolve())

    monkeypatch.setenv('UV', str(custom_uv))
    assert deploy.resolve_uv() == str(custom_uv.resolve())
    monkeypatch.delenv('UV')

    with pytest.raises(ValueError, match='not found'):
        deploy.resolve_uv(str(tmp_path / 'nonexistent_uv'))


def test_deployment_uses_uv_when_configured(tmp_path, monkeypatch):
    source, target, state = (tmp_path / name for name in ('source', 'app', 'state'))
    source.mkdir()
    target.mkdir()
    venv = target / '.venv'
    (venv / 'bin').mkdir(parents=True)
    (venv / 'pyvenv.cfg').write_text('version = 3.15.2\n')

    commands = []
    def run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(deploy.subprocess, 'run', run)
    monkeypatch.setattr(deploy, 'python_version', lambda path: (3, 15))
    monkeypatch.setattr(deploy, 'sync_files', lambda *args: None)
    monkeypatch.setattr(deploy, 'merge_environment', lambda *args, **kwargs: None)
    monkeypatch.setattr(deploy, 'change_owner', lambda *args, **kwargs: None)

    deployment = deploy.Deployment(source, target, state, False, python_bin='/mock/python', uv_bin='/mock/uv')
    deploy.deploy(deployment, restart=False)
    python = str(venv / 'bin/python')
    assert ['/mock/uv', 'pip', 'install', '--link-mode=copy', '--python', python, '-r', 'requirements.txt'] in commands
    assert not any(cmd[1:3] == ['-m', 'pip'] for cmd in commands)


def test_main_handles_uv_and_no_uv_flags(tmp_path, monkeypatch):
    custom_uv = tmp_path / 'uv'
    custom_uv.write_text('#!/bin/sh\n')
    custom_uv.chmod(0o755)

    calls = []
    monkeypatch.setattr(deploy.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(deploy, 'python_version', lambda path: (3, 15))
    monkeypatch.setattr(deploy, 'deploy', lambda deployment, **kwargs: calls.append(deployment))

    assert deploy.main([str(tmp_path / 'app'), '--uv', str(custom_uv), '--no-restart']) == 0
    assert calls[-1].uv_bin == str(custom_uv.resolve())

    assert deploy.main([str(tmp_path / 'app'), '--no-uv', '--no-restart']) == 0
    assert calls[-1].uv_bin is None
