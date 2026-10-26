#!/usr/bin/env python3
"""Build a deterministic release from a clean, committed allowlist."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    'bin/envctl', 'bin/bakctl', 'scripts/install.py', 'scripts/install.sh',
    'README.md', 'LICENSE', 'NOTICE', 'CHANGELOG.md', 'VERSION',
    'docs/architecture.md', 'docs/security-model.md',
    'docs/format-and-compatibility.md', 'docs/integrations.md',
    'docs/publication-checks.md',
    'examples/example.env', 'examples/.sops.yaml',
]


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    if git('status', '--porcelain', '--untracked-files=normal'):
        parser.error('commit all source changes before building')
    version = (ROOT / 'VERSION').read_text().strip()
    commit = git('rev-parse', 'HEAD')
    payload = {name: (ROOT / name).read_bytes() for name in FILES}
    metadata = {'version': version, 'commit': commit}
    payload['RELEASE.json'] = (json.dumps(metadata, indent=2) + '\n').encode()
    manifest = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(payload.items())}
    payload['MANIFEST.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
    args.output.mkdir(parents=True, exist_ok=True)
    archive = args.output / f'envctl-v{version}.tar.gz'
    with open(archive, 'wb') as raw, gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as tar:
            for name, data in sorted(payload.items()):
                info = tarfile.TarInfo(f'envctl-v{version}/{name}')
                info.size = len(data)
                info.mode = 0o755 if name.startswith('bin/') or name.endswith('.sh') else 0o644
                tar.addfile(info, io.BytesIO(data))
    (args.output / 'MANIFEST.json').write_bytes(payload['MANIFEST.json'])
    (args.output / 'RELEASE.json').write_bytes(payload['RELEASE.json'])
    assets = [archive.name, 'MANIFEST.json', 'RELEASE.json']
    checksums = ''.join(hashlib.sha256((args.output / name).read_bytes()).hexdigest() + '  ' + name + '\n' for name in assets)
    (args.output / 'SHA256SUMS').write_text(checksums)
    print(archive)


if __name__ == '__main__':
    main()
