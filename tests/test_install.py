"""Verify activation, rollback and tamper rejection in disposable prefixes."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def package(tmp_path, version):
    root = tmp_path / ('release-' + version)
    root.mkdir()
    for name in ('bin/envctl', 'bin/bakctl', 'scripts/install.py', 'LICENSE'):
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
                          capture_output=True, text=True)


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
