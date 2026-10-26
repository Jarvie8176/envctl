"""Release contracts; synthetic data and disposable age identities only."""
import fcntl
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'bin/envctl'
BAK = ROOT / 'bin/bakctl'


def run(path, *args, env=None, input=None):
    return subprocess.run([sys.executable, str(CLI), '--file', str(path), *args],
                          input=input, text=True, capture_output=True, env=env)


@pytest.fixture
def crypto(tmp_path):
    assert shutil.which('sops'), 'SOPS is required for release acceptance'
    assert shutil.which('age-keygen'), 'age-keygen is required for release acceptance'
    env = {k: v for k, v in os.environ.items() if not k.startswith('SOPS_')}
    env.update(HOME=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path / 'config'),
               ENVCTL_SOPS_BIN=shutil.which('sops'))
    recipients = []
    for n in (1, 2):
        key = tmp_path / f'identity{n}.txt'
        subprocess.run(['age-keygen', '-o', str(key)], check=True, capture_output=True)
        result = subprocess.run(['age-keygen', '-y', str(key)], check=True,
                                capture_output=True, text=True)
        recipients.append(result.stdout.strip())
    env['SOPS_AGE_KEY_FILE'] = str(tmp_path / 'identity1.txt')
    (tmp_path / '.sops.yaml').write_text('creation_rules:\n  - age: ' + recipients[0] + '\n')
    return tmp_path / 'test.env.enc', env, recipients


def test_encrypted_write_read_backup_restore_exec(crypto):
    path, env, _ = crypto
    assert run(path, 'set', 'TOKEN', input='synthetic-first\n', env=env).returncode == 0
    first = path.read_bytes()
    assert b'synthetic-first' not in first
    assert path.stat().st_mode & 0o777 == 0o600
    assert run(path, 'get', 'TOKEN', env=env).stdout == 'synthetic-first\n'
    assert run(path, 'set', 'TOKEN', input='synthetic-second', env=env).returncode == 0
    backups = list(path.parent.glob(path.name + '.bak.*'))
    assert len(backups) == 1 and backups[0].read_bytes() == first
    assert run(path, 'list', env=env).stdout == 'TOKEN\n'
    assert 'synthetic-second' in run(path, 'export', env=env).stdout
    child = run(path, 'exec', '--', sys.executable, '-c',
                'import os; assert os.environ["TOKEN"] == "synthetic-second"', env=env)
    assert child.returncode == 0
    assert run(path, 'restore', backups[0].name.rsplit('.', 1)[1], env=env).returncode == 0
    assert path.read_bytes() == first
    assert run(path, 'get', 'TOKEN', env=env).stdout == 'synthetic-first\n'
    assert run(path, 'unset', 'TOKEN', env=env).returncode == 0
    assert run(path, 'get', 'TOKEN', env=env).returncode == 1


@pytest.mark.parametrize('failure', ['wrong-key', 'recipient-change', 'sops-failure'])
def test_encryption_failure_preserves_carrier(crypto, failure):
    path, env, recipients = crypto
    assert run(path, 'set', 'TOKEN', input='synthetic-old', env=env).returncode == 0
    before = path.read_bytes()
    if failure == 'wrong-key':
        env['SOPS_AGE_KEY_FILE'] = str(path.parent / 'identity2.txt')
    elif failure == 'recipient-change':
        (path.parent / '.sops.yaml').write_text('creation_rules:\n  - age: ' + recipients[1] + '\n')
    else:
        env['ENVCTL_SOPS_BIN'] = '/usr/bin/false'
    result = run(path, 'set', 'TOKEN', input='synthetic-new', env=env)
    assert result.returncode == 1
    assert path.read_bytes() == before
    assert 'synthetic-' not in result.stdout + result.stderr
    assert 'Traceback' not in result.stderr


def test_new_file_with_inaccessible_recipient_not_created(crypto):
    path, env, recipients = crypto
    (path.parent / '.sops.yaml').write_text('creation_rules:\n  - age: ' + recipients[1] + '\n')
    assert run(path, 'set', 'A', input='synthetic', env=env).returncode == 1
    assert not path.exists()


@pytest.mark.parametrize('value', ['a\nB=injected', 'a\rb', 'a\0b'])
def test_multiline_or_nul_rejected(tmp_path, value):
    path = tmp_path / 'test.env'
    path.write_text('A=old\n')
    result = run(path, 'set', 'A', input=value)
    assert result.returncode == 1
    assert path.read_text() == 'A=old\n'


def test_invalid_keys_cannot_inject_export(tmp_path):
    path = tmp_path / 'test.env'
    path.write_text('A=ok\nB;false=bad\nexport C=bad\n')
    assert run(path, 'export').stdout == 'export A=ok\n'
    assert run(path, 'set', 'B;false', input='bad').returncode == 2


def test_lock_contention_refuses_without_changing_file(tmp_path):
    path = tmp_path / 'test.env'
    path.write_text('A=old\n')
    with open(str(path) + '.envctl.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        assert run(path, 'set', 'A', input='new').returncode == 1
    assert path.read_text() == 'A=old\n'
    assert run(path, 'set', 'A', input='new').returncode == 0


def test_symlink_and_hardlink_mutations_refused(tmp_path):
    target = tmp_path / 'target'
    target.write_text('A=old\n')
    path = tmp_path / 'test.env'
    path.symlink_to(target)
    assert run(path, 'set', 'A', input='new').returncode == 1
    path.unlink()
    os.link(target, path)
    assert run(path, 'set', 'A', input='new').returncode == 1
    assert target.read_text() == 'A=old\n'


def test_modes_and_backup_failure_protect_target(tmp_path):
    path = tmp_path / 'test.env'
    assert run(path, 'set', 'A', input='old').returncode == 0
    assert path.stat().st_mode & 0o777 == 0o600
    path.chmod(0o640)
    assert run(path, 'set', 'A', input='new').returncode == 0
    assert path.stat().st_mode & 0o777 == 0o640
    isolated = tmp_path / 'tools'
    isolated.mkdir()
    shutil.copy2(CLI, isolated / 'envctl')
    # Missing sibling bakctl must prevent writes.
    result = subprocess.run([sys.executable, str(isolated / 'envctl'), '--file', str(path),
                             'set', 'A'], input='never-written', text=True, capture_output=True)
    assert result.returncode == 1
    assert path.read_text() == 'A=new\n'


def test_replace_failure_cleans_candidate_and_preserves_original(tmp_path, monkeypatch):
    loader = importlib.machinery.SourceFileLoader('envctl_failure', str(CLI))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    path = tmp_path / 'test.env'
    path.write_text('A=old\n')
    def fail(*args):
        raise OSError('synthetic interruption before rename')
    monkeypatch.setattr(module.os, 'replace', fail)
    with pytest.raises(OSError):
        module._write(path, ['A=new'])
    assert path.read_text() == 'A=old\n'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['test.env']


def test_paired_versions():
    for tool in (CLI, BAK):
        result = subprocess.run([sys.executable, str(tool), '--version'], capture_output=True, text=True)
        assert result.returncode == 0
        assert '0.1.0' in result.stdout
