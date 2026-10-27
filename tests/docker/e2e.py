#!/usr/bin/env python3
"""Black-box acceptance of a packaged installation in a disposable container."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile


def require(condition, label):
    if not condition:
        raise AssertionError(label)


def main():
    results = []
    def passed(name):
        results.append(name)
        print('PASS ' + name, flush=True)

    require(os.geteuid() != 0, 'test must run as non-root')
    require(importlib.util.find_spec('yaml') is None, 'test default mode without PyYAML')
    require(not Path('/opt/package/.git').exists(), 'no source checkout')
    passed('non-root runtime without PyYAML or a source checkout')
    assets = Path('/opt/package')
    for line in (assets / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split('  ', 1)
        require(Path(name).name == name, 'safe checksum filename')
        require(hashlib.sha256((assets / name).read_bytes()).hexdigest() == digest, 'asset hash')
    with tempfile.TemporaryDirectory(prefix='envctl-e2e-') as scratch:
        root = Path(scratch)
        archives = list(assets.glob('*.tar.gz'))
        require(len(archives) == 1, 'one package')
        with tarfile.open(archives[0]) as archive:
            for member in archive.getmembers():
                name = Path(member.name)
                require(member.isfile() and not name.is_absolute() and '..' not in name.parts,
                        'safe archive entries')
            archive.extractall(root / 'unpacked', filter='data')
        release = next((root / 'unpacked').iterdir())
        metadata = json.loads((release / 'RELEASE.json').read_text())
        require(set(metadata) == {'version', 'commit'}, 'public provenance schema')
        require(metadata == json.loads((assets / 'RELEASE.json').read_text()), 'matching provenance')
        for name, digest in json.loads((release / 'MANIFEST.json').read_text()).items():
            require(hashlib.sha256((release / name).read_bytes()).hexdigest() == digest, 'member hash')
        passed('download-style assets, archive, manifest and public provenance')

        prefix = root / 'prefix with spaces'
        home = root / 'home'
        home.mkdir(mode=0o700)
        env = {'PATH': os.environ['PATH'], 'HOME': str(home), 'LANG': 'C.UTF-8',
               'XDG_CONFIG_HOME': str(home / '.config')}

        def call(argv, *, expected=0, input=None, settings=None):
            result = subprocess.run([str(arg) for arg in argv], input=input, text=True,
                                    capture_output=True, env=settings or env, timeout=30)
            require(result.returncode == expected, 'unexpected exit from ' + str(argv[0]))
            return result

        def install(source, expected=0):
            return call(['sh', source / 'scripts/install.sh', '--prefix', prefix], expected=expected)

        install(release)
        cli = prefix / 'bin/envctl'
        bak = prefix / 'bin/bakctl'
        def command(path, *args, **kwargs):
            return call([cli, '--file', path, *args], **kwargs)

        def versions(version):
            for tool in (cli, bak):
                output = call([tool, '--version']).stdout
                require(version in output and metadata['commit'] in output, 'installed source version')
            require(cli.resolve().parent == bak.resolve().parent, 'same-version sibling pairing')

        versions(metadata['version'])
        install(release)
        passed('fresh install, idempotent reinstall and executable source versions')
        plain = root / 'plain.env'
        command(plain, 'set', 'A', input='synthetic-one\n')
        require(plain.stat().st_mode & 0o777 == 0o600, 'new plaintext mode')
        require(command(plain, 'get', 'A').stdout == 'synthetic-one\n', 'plaintext get')
        command(plain, 'set', 'A', input='synthetic-one')
        require(not list(root.glob('plain.env.bak.*')), 'no-op makes no backup')
        plain.chmod(0o640)
        command(plain, 'set', 'A', input='synthetic-two')
        require(plain.stat().st_mode & 0o777 == 0o640, 'existing mode retained')
        require(len(list(root.glob('plain.env.bak.*'))) == 1, 'plaintext backup')
        command(plain, 'unset', 'A')
        command(plain, 'get', 'A', expected=1)
        passed('plaintext CRUD, idempotency, permissions and default backups')

        recipients = []
        for number in (1, 2):
            key = root / f'identity{number}.txt'
            call(['age-keygen', '-o', key])
            recipients.append(call(['age-keygen', '-y', key]).stdout.strip())
        env['SOPS_AGE_KEY_FILE'] = str(root / 'identity1.txt')
        config = root / '.sops.yaml'
        def recipient(number):
            config.write_text('creation_rules:\n  - age: ' + recipients[number - 1] + '\n')
        recipient(1)
        encrypted = root / 'app.env.enc'
        command(encrypted, 'set', 'TOKEN', input='synthetic-first')
        original = encrypted.read_bytes()
        require(b'synthetic-first' not in original, 'ciphertext carrier')
        require(encrypted.stat().st_mode & 0o777 == 0o600, 'new ciphertext mode')
        result = command(encrypted, 'set', 'TOKEN', input='synthetic-second')
        require('synthetic-' not in result.stderr, 'no value echoed by mutation')
        require(command(encrypted, 'get', 'TOKEN').stdout == 'synthetic-second\n', 'decrypt')
        require(command(encrypted, 'list').stdout == 'TOKEN\n', 'keys-only list')
        require('synthetic-second' in command(encrypted, 'export').stdout, 'explicit export')
        command(encrypted, 'exec', '--', sys.executable, '-c',
                'import os; assert os.environ["TOKEN"] == "synthetic-second"')
        command(encrypted, 'exec', '--', sys.executable, '-c', 'raise SystemExit(7)', expected=7)
        history = command(encrypted, 'history').stdout
        backups = list(root.glob('app.env.enc.bak.*'))
        require(len(backups) == 1 and backups[0].read_bytes() == original, 'ciphertext backup')
        timestamp = backups[0].name.rsplit('.', 1)[1]
        require(timestamp in history, 'history timestamp')
        command(encrypted, 'restore', timestamp)
        require(encrypted.read_bytes() == original, 'restore exact ciphertext')
        require(command(encrypted, 'get', 'TOKEN').stdout == 'synthetic-first\n', 'decrypt restored')
        require(list(root.glob('app.env.enc.pre-restore.*')), 'pre-restore snapshot')
        passed('real SOPS CRUD, exec environment/status, ciphertext backup and restore')

        before = encrypted.read_bytes()
        for label, settings in (
            ('wrong identity', {**env, 'SOPS_AGE_KEY_FILE': str(root / 'identity2.txt')}),
            ('failed SOPS', {**env, 'ENVCTL_SOPS_BIN': '/bin/false'}),
        ):
            result = command(encrypted, 'set', 'TOKEN', input='synthetic-rejected', settings=settings, expected=1)
            require(encrypted.read_bytes() == before, label + ' protects carrier')
            require('synthetic-' not in result.stdout + result.stderr, label + ' safe error')
        recipient(2)
        command(encrypted, 'set', 'TOKEN', input='synthetic-rejected', expected=1)
        require(encrypted.read_bytes() == before, 'recipient self-lockout protection')
        missing = root / 'missing.env.enc'
        command(missing, 'set', 'A', input='synthetic-rejected', expected=1)
        require(not missing.exists(), 'inaccessible new recipient refuses creation')
        recipient(1)
        passed('wrong key, failed SOPS and recipient changes preserve carriers')

        for value in ('two\nlines', 'two\rlines', 'null\0value'):
            command(encrypted, 'set', 'TOKEN', input=value, expected=1)
        command(encrypted, 'set', 'BAD;KEY', input='synthetic', expected=2)
        require(encrypted.read_bytes() == before, 'invalid input preserves ciphertext')
        with open(str(encrypted) + '.envctl.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            command(encrypted, 'set', 'TOKEN', input='synthetic-rejected', expected=1)
        require(encrypted.read_bytes() == before, 'lock contention preserves ciphertext')
        command(encrypted, 'unset', 'TOKEN')
        command(encrypted, 'get', 'TOKEN', expected=1)
        passed('invalid input, cooperating-writer lock and empty encrypted document')

        protected = root / 'protected'
        protected.mkdir()
        target = protected / 'plain.env'
        target.write_text('A=synthetic\n')
        protected.chmod(0o500)
        try:
            command(target, 'set', 'A', input='rejected', expected=1)
            require(target.read_text() == 'A=synthetic\n', 'unwritable directory protection')
        finally:
            protected.chmod(0o700)
        link = root / 'linked.env'
        link.symlink_to(target)
        command(link, 'set', 'A', input='rejected', expected=1)
        link.unlink()
        os.link(target, link)
        command(link, 'set', 'A', input='rejected', expected=1)
        link.unlink()
        isolated = root / 'isolated'
        isolated.mkdir()
        shutil.copy2(cli.resolve(), isolated / 'envctl')
        call([isolated / 'envctl', '--file', target, 'set', 'A'], input='rejected', expected=1)
        require(target.read_text() == 'A=synthetic\n', 'missing sibling backup protects file')
        passed('real filesystem permissions, linked targets and missing backup tool')

        upgrade = root / 'synthetic-upgrade'
        shutil.copytree(release, upgrade)
        next_version = metadata['version'] + '-e2e'
        next_metadata = {**metadata, 'version': next_version}
        (upgrade / 'RELEASE.json').write_text(json.dumps(next_metadata))
        for name in ('envctl', 'bakctl'):
            tool = upgrade / 'bin' / name
            tool.write_text(tool.read_text().replace(f'VERSION = "{metadata["version"]}"',
                                                     f'VERSION = "{next_version}"'))
        manifest = json.loads((upgrade / 'MANIFEST.json').read_text())
        for name in manifest:
            manifest[name] = hashlib.sha256((upgrade / name).read_bytes()).hexdigest()
        (upgrade / 'MANIFEST.json').write_text(json.dumps(manifest))
        install(upgrade)
        versions(next_version)
        install(release)
        versions(metadata['version'])
        copied = root / 'copied-install'
        shutil.copytree(release, copied)
        call([copied / 'bin/envctl', '--file', target, 'set', 'A'], input='synthetic-copied')
        require(target.read_text() == 'A=synthetic-copied\n', 'copied sibling pairing')
        passed('synthetic next-version upgrade, rollback and copied installation')

        (upgrade / 'bin/bakctl').write_text('tampered\n')
        install(upgrade, expected=1)
        versions(metadata['version'])
        require(os.readlink(prefix / 'lib/envctl/current') == metadata['version'], 'failed upgrade keeps active version')
        passed('tampered upgrade rejected without activation change')
        print(json.dumps({'result': 'passed', 'cases': len(results), 'checks': results,
                          'source_commit': metadata['commit'], 'version': metadata['version'],
                          'python': sys.version.split()[0], 'uid': os.geteuid(),
                          'pypi_dependencies': [], 'upgrade_fixture': next_version}, indent=2))


if __name__ == '__main__':
    main()
