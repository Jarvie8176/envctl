"""Verify activation, rollback and tamper rejection in disposable prefixes."""
import errno
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def package(tmp_path, version):
    root = tmp_path / ('release-' + version)
    root.mkdir()
    for name in ('bin/envctl', 'bin/bakctl', 'scripts/install.py', 'scripts/install.sh', 'LICENSE'):
        dest = root / name
        dest.parent.mkdir(exist_ok=True, parents=True)
        shutil.copy2(ROOT / name, dest)
        if name.startswith('bin/'):
            dest.write_text(dest.read_text().replace('VERSION = "0.1.0"', f'VERSION = "{version}"'))
    (root / 'RELEASE.json').write_text(json.dumps({'version': version, 'commit': 'a' * 40}))
    manifest = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in root.rglob('*') if p.is_file()}
    (root / 'MANIFEST.json').write_text(json.dumps(manifest))
    return root


def install(root, prefix):
    return subprocess.run([sys.executable, str(root / 'scripts/install.py'), '--prefix', str(prefix)],
                          capture_output=True, text=True, timeout=10)


def version(prefix, name):
    return subprocess.check_output([str(prefix / 'bin' / name), '--version'], text=True)


def test_fresh_install_upgrade_rollback_and_copied_pair(tmp_path):
    first = package(tmp_path, '0.1.0')
    second = package(tmp_path, '0.2.0')
    prefix = tmp_path / 'local prefix with spaces'
    for root, expected in ((first, '0.1.0'), (first, '0.1.0'), (second, '0.2.0'), (first, '0.1.0')):
        result = install(root, prefix)
        assert result.returncode == 0, result.stderr
        assert expected in version(prefix, 'envctl')
        assert expected in version(prefix, 'bakctl')
    target = tmp_path / 'test.env'
    target.write_text('A=first\n')
    result = subprocess.run([str(prefix / 'bin/envctl'), '--file', str(target), 'set', 'A'],
                            input='second', text=True, capture_output=True)
    assert result.returncode == 0
    assert list(tmp_path.glob('test.env.bak.*'))
    # Copy-based enrollment still resolves its paired sibling, without a checkout.
    copied = tmp_path / 'copied'
    shutil.copytree(first, copied)
    result = subprocess.run([str(copied / 'bin/envctl'), '--file', str(target), 'set', 'A'],
                            input='third', text=True, capture_output=True)
    assert result.returncode == 0


def test_tamper_and_unmanaged_commands_are_refused(tmp_path):
    root = package(tmp_path, '0.1.0')
    prefix = tmp_path / 'prefix'
    (prefix / 'bin').mkdir(parents=True)
    old = prefix / 'bin/envctl'
    old.write_text('unmanaged')
    assert install(root, prefix).returncode == 1
    assert old.read_text() == 'unmanaged'
    old.unlink()
    (root / 'bin/bakctl').write_text('tampered')
    assert install(root, prefix).returncode == 1
    assert not (prefix / 'lib/envctl/current').exists()


def test_changed_same_version_refused_without_switching(tmp_path):
    root = package(tmp_path, '0.1.0')
    prefix = tmp_path / 'prefix'
    assert install(root, prefix).returncode == 0
    installed = prefix / 'lib/envctl/0.1.0/bin/envctl'
    installed.write_text(installed.read_text() + '\n# local modification\n')
    assert install(root, prefix).returncode == 1
    assert os.readlink(prefix / 'lib/envctl/current') == '0.1.0'


def reseal(root):
    manifest = json.loads((root / 'MANIFEST.json').read_text())
    for name in manifest:
        manifest[name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    (root / 'MANIFEST.json').write_text(json.dumps(manifest))


def test_missing_commit_is_rejected_before_activation(tmp_path):
    root = package(tmp_path, '0.1.0')
    (root / 'RELEASE.json').write_text('{"version": "0.1.0"}')
    reseal(root)
    prefix = tmp_path / 'prefix'
    result = install(root, prefix)
    assert result.returncode == 1
    assert not os.path.lexists(prefix / 'lib/envctl/current')
    assert not os.path.lexists(prefix / 'bin/envctl')


def test_symlinked_manifest_parent_is_rejected(tmp_path):
    root = package(tmp_path, '0.1.0')
    outside = tmp_path / 'outside-bin'
    (root / 'bin').rename(outside)
    (root / 'bin').symlink_to(outside, target_is_directory=True)
    result = install(root, tmp_path / 'prefix')
    assert result.returncode == 1
    assert 'Traceback' not in result.stderr


def test_unmanaged_current_file_is_preserved(tmp_path):
    root = package(tmp_path, '0.1.0')
    prefix = tmp_path / 'prefix'
    current = prefix / 'lib/envctl/current'
    current.parent.mkdir(parents=True)
    current.write_text('unmanaged')
    result = install(root, prefix)
    assert result.returncode == 1
    assert not current.is_symlink()
    assert current.read_text() == 'unmanaged'


@pytest.mark.parametrize('metadata', [None, [], {}, {'version': None},
    {'version': '0.1.0'}, {'version': '0.1.0', 'commit': 1},
    {'version': '0.1.0', 'commit': 'bad'}, {'version': '../escape', 'commit': 'a' * 40},
    {'version': 1, 'commit': 'a' * 40}])
def test_invalid_metadata_preserves_active_installation(tmp_path, metadata):
    first = package(tmp_path, '0.1.0')
    next_release = package(tmp_path, '0.2.0')
    prefix = tmp_path / 'prefix'
    assert install(first, prefix).returncode == 0
    (next_release / 'RELEASE.json').write_text(json.dumps(metadata))
    reseal(next_release)
    result = install(next_release, prefix)
    assert result.returncode == 1
    assert 'Traceback' not in result.stderr
    assert os.readlink(prefix / 'lib/envctl/current') == '0.1.0'
    for name in ('envctl', 'bakctl'):
        assert '0.1.0' in version(prefix, name)
    assert not (prefix / 'lib/envctl/0.2.0').exists()


@pytest.mark.parametrize('manifest', ['null', '[]', '{}', '{', '{"x":null}',
    '{"x":[]}', '{"x":"invalid"}', '{"x":1,"x":2}'])
def test_malformed_manifest_has_clean_error(tmp_path, manifest):
    root = package(tmp_path, '0.1.0')
    (root / 'MANIFEST.json').write_text(manifest)
    prefix = tmp_path / 'prefix'
    result = install(root, prefix)
    assert result.returncode == 1
    assert 'Traceback' not in result.stderr
    assert not prefix.exists()


@pytest.mark.parametrize('name', ['', '.', '../outside', '/outside', 'bin/../LICENSE',
                                  './LICENSE', 'bin//envctl', 'LICENSE/', 'MANIFEST.json'])
def test_unsafe_manifest_paths(tmp_path, name):
    root = package(tmp_path, '0.1.0')
    manifest = json.loads((root / 'MANIFEST.json').read_text())
    manifest[name] = 'a' * 64
    (root / 'MANIFEST.json').write_text(json.dumps(manifest))
    result = install(root, tmp_path / 'prefix')
    assert result.returncode == 1
    assert 'Traceback' not in result.stderr


@pytest.mark.parametrize('name', ['MANIFEST.json', 'RELEASE.json', 'bin/bakctl'])
@pytest.mark.parametrize('kind', ['symlink', 'directory', 'fifo', 'missing'])
def test_nonregular_release_members_rejected_without_hanging(tmp_path, name, kind):
    root = package(tmp_path, '0.1.0')
    target = root / name
    outside = tmp_path / 'outside'
    target.rename(outside)
    if kind == 'symlink':
        target.symlink_to(outside)
    elif kind == 'directory':
        target.mkdir()
    elif kind == 'fifo':
        os.mkfifo(target)
    result = install(root, tmp_path / 'prefix')
    assert result.returncode == 1
    assert 'Traceback' not in result.stderr


def test_duplicate_metadata_keys_rejected(tmp_path):
    root = package(tmp_path, '0.1.0')
    (root / 'RELEASE.json').write_text('{"version":"0.1.0","version":"0.2.0","commit":"' + 'a' * 40 + '"}')
    reseal(root)
    assert install(root, tmp_path / 'prefix').returncode == 1


@pytest.mark.parametrize('location', ['lib', 'lib/envctl', 'bin', 'lib/envctl/current',
                                    'lib/envctl/.install.lock', 'lib/envctl/0.1.0'])
def test_redirected_installation_paths_rejected(tmp_path, location):
    root = package(tmp_path, '0.1.0')
    prefix = tmp_path / 'prefix'
    outside = tmp_path / 'outside'
    outside.mkdir()
    target = prefix / location
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(outside, target_is_directory=True)
    result = install(root, prefix)
    assert result.returncode == 1
    assert target.is_symlink()
    assert list(outside.iterdir()) == []
    assert 'Traceback' not in result.stderr


@pytest.mark.parametrize('kind', ['fifo', 'hardlink'])
def test_unsafe_lock_rejected(tmp_path, kind):
    root = package(tmp_path, '0.1.0')
    prefix = tmp_path / 'prefix'
    lock = prefix / 'lib/envctl/.install.lock'
    lock.parent.mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.write_text('unchanged')
    if kind == 'fifo':
        os.mkfifo(lock)
    else:
        os.link(outside, lock)
    result = install(root, prefix)
    assert result.returncode == 1
    assert 'Traceback' not in result.stderr
    assert outside.read_text() == 'unchanged'


def test_installation_lock_contention_and_retry(tmp_path):
    first = package(tmp_path, '0.1.0')
    second = package(tmp_path, '0.2.0')
    prefix = tmp_path / 'prefix'
    assert install(first, prefix).returncode == 0
    with open(prefix / 'lib/envctl/.install.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = install(second, prefix)
        assert result.returncode == 1
        assert 'another installation' in result.stderr
        assert os.readlink(prefix / 'lib/envctl/current') == '0.1.0'
    assert install(second, prefix).returncode == 0
    assert '0.2.0' in version(prefix, 'envctl')


def test_same_version_different_package_and_nonexecutable_command_refused(tmp_path):
    root = package(tmp_path, '0.1.0')
    prefix = tmp_path / 'prefix'
    assert install(root, prefix).returncode == 0
    installed = prefix / 'lib/envctl/0.1.0/bin/envctl'
    installed.chmod(0o644)
    result = install(root, prefix)
    assert result.returncode == 1
    assert 'not executable' in result.stderr
    installed.chmod(0o755)
    (root / 'RELEASE.json').write_text(json.dumps({'version': '0.1.0', 'commit': 'b' * 40}))
    reseal(root)
    assert install(root, prefix).returncode == 1
    assert 'a' * 40 in (prefix / 'lib/envctl/0.1.0/RELEASE.json').read_text()


@pytest.fixture
def installer():
    spec = importlib.util.spec_from_file_location('installer', ROOT / 'scripts/install.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('previous', [False, True])
@pytest.mark.parametrize('failure', ['copy', 'link', 'switch'])
def test_interrupted_install_cleans_partial_links_and_keeps_previous(tmp_path, monkeypatch, installer, previous, failure):
    prefix = tmp_path / 'prefix'
    if previous:
        installer.activate(package(tmp_path, '0.1.0'), prefix)
    root = package(tmp_path, '0.2.0')
    original_link = Path.symlink_to
    def fail(*args, **kwargs):
        raise OSError(errno.ENOSPC, 'injected storage failure')
    def link(self, *args, **kwargs):
        if self.name == 'bakctl':
            fail()
        return original_link(self, *args, **kwargs)
    if failure == 'link' and previous:
        # A missing managed command is repaired, but a failed repair must not switch versions.
        (prefix / 'bin/bakctl').unlink()
    with monkeypatch.context() as patch:
        if failure == 'copy':
            patch.setattr(installer.shutil, 'copy2', fail)
        elif failure == 'link':
            patch.setattr(Path, 'symlink_to', link)
        else:
            patch.setattr(installer.os, 'replace', fail)
        with pytest.raises(OSError, match='injected storage failure'):
            installer.activate(root, prefix)
    base = prefix / 'lib/envctl'
    assert not list(base.glob('.install-*'))
    assert not list(base.glob('.current-*'))
    if previous:
        assert os.readlink(base / 'current') == '0.1.0'
        assert '0.1.0' in version(prefix, 'envctl')
    else:
        assert not os.path.lexists(base / 'current')
        for name in ('envctl', 'bakctl'):
            assert not os.path.lexists(prefix / 'bin' / name)
    installer.activate(root, prefix)
    for name in ('envctl', 'bakctl'):
        assert '0.2.0' in version(prefix, name)


def test_source_changed_during_copy_is_not_activated(tmp_path, monkeypatch, installer):
    root = package(tmp_path, '0.1.0')
    prefix = tmp_path / 'prefix'
    copy = installer.shutil.copy2
    def changed_copy(source, destination):
        if Path(source).name == 'RELEASE.json':
            (root / 'RELEASE.json').write_text(json.dumps({'version': '0.2.0', 'commit': 'b' * 40}))
            reseal(root)
        return copy(source, destination)
    monkeypatch.setattr(installer.shutil, 'copy2', changed_copy)
    with pytest.raises(ValueError, match='changed during installation'):
        installer.activate(root, prefix)
    assert not os.path.lexists(prefix / 'lib/envctl/current')
    assert not (prefix / 'lib/envctl/0.1.0').exists()


@pytest.mark.parametrize('prefix_arg', [None, 'relative prefix', '~/tilde prefix'])
def test_shell_wrapper_source_spaces_default_relative_and_tilde_prefix(tmp_path, prefix_arg):
    root = package(tmp_path, '0.1.0')
    spaced = tmp_path / 'source with spaces'
    root.rename(spaced)
    home = tmp_path / 'home'
    home.mkdir()
    args = ['sh', str(spaced / 'scripts/install.sh')]
    if prefix_arg is not None:
        args += ['--prefix', prefix_arg]
    result = subprocess.run(args, cwd=tmp_path, env={**os.environ, 'HOME': str(home)},
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    expected = home / '.local' if prefix_arg is None else (
        home / 'tilde prefix' if prefix_arg.startswith('~') else tmp_path / prefix_arg)
    for name in ('envctl', 'bakctl'):
        assert '0.1.0' in version(expected, name)


@pytest.mark.parametrize('kind', ['file', 'directory', 'symlink', 'dangling'])
def test_unmanaged_second_command_does_not_create_first(tmp_path, kind):
    root = package(tmp_path, '0.1.0')
    prefix = tmp_path / 'prefix'
    target = prefix / 'bin/bakctl'
    target.parent.mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.write_text('unchanged')
    if kind == 'file':
        target.write_text('unmanaged')
    elif kind == 'directory':
        target.mkdir()
    else:
        target.symlink_to(outside if kind == 'symlink' else tmp_path / 'missing')
    assert install(root, prefix).returncode == 1
    assert not os.path.lexists(prefix / 'bin/envctl')
    assert not os.path.lexists(prefix / 'lib/envctl/current')
    assert outside.read_text() == 'unchanged'


def test_existing_file_prefix_is_not_overwritten(tmp_path):
    root = package(tmp_path, '0.1.0')
    prefix = tmp_path / 'prefix'
    prefix.write_text('unmanaged')
    result = install(root, prefix)
    assert result.returncode == 1
    assert 'Traceback' not in result.stderr
    assert prefix.read_text() == 'unmanaged'
