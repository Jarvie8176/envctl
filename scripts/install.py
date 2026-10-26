#!/usr/bin/env python3
"""Install a verified local release; no downloads or privileged writes."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile


def verify(root):
    manifest = json.loads((root / 'MANIFEST.json').read_text())
    for name, digest in manifest.items():
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or (root / path).is_symlink():
            raise ValueError('unsafe manifest entry')
        if hashlib.sha256((root / path).read_bytes()).hexdigest() != digest:
            raise ValueError('release checksum mismatch: ' + name)
    for required in ('bin/envctl', 'bin/bakctl', 'RELEASE.json', 'LICENSE'):
        if required not in manifest:
            raise ValueError('incomplete release manifest')
    metadata = json.loads((root / 'RELEASE.json').read_text())
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[a-zA-Z0-9.]+)?', metadata['version']):
        raise ValueError('invalid release version')
    return metadata, manifest


def activate(root, prefix):
    metadata, manifest = verify(root)
    base = prefix / 'lib/envctl'
    bindir = prefix / 'bin'
    base.mkdir(parents=True, exist_ok=True)
    bindir.mkdir(parents=True, exist_ok=True)
    with open(base / '.install.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Refuse existing unmanaged commands before any activation change.
        for name in ('envctl', 'bakctl'):
            link = bindir / name
            target = str(base / 'current/bin' / name)
            if os.path.lexists(link) and (not link.is_symlink() or os.readlink(link) != target):
                raise ValueError('refusing to replace unmanaged command: ' + str(link))
        dest = base / metadata['version']
        if os.path.lexists(dest):
            if dest.is_symlink() or verify(dest) != (metadata, manifest):
                raise ValueError('version already installed with different contents')
        else:
            staging = Path(tempfile.mkdtemp(prefix='.install-', dir=base))
            try:
                for name in (*manifest, 'MANIFEST.json'):
                    output = staging / name
                    output.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(root / name, output)
                verify(staging)
                for name in ('envctl', 'bakctl'):
                    (staging / 'bin' / name).chmod(0o755)
                staging.rename(dest)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        for name in ('envctl', 'bakctl'):
            link = bindir / name
            if not os.path.lexists(link):
                link.symlink_to(base / 'current/bin' / name)
        fd, temp = tempfile.mkstemp(prefix='.current-', dir=base)
        os.close(fd)
        os.unlink(temp)
        try:
            os.symlink(metadata['version'], temp)
            os.replace(temp, base / 'current')
        finally:
            if os.path.lexists(temp):
                os.unlink(temp)
    print(f"Installed envctl + bakctl {metadata['version']} ({metadata['commit']}) to {prefix}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', type=Path, default=Path.home() / '.local')
    args = parser.parse_args()
    try:
        activate(Path(__file__).resolve().parents[1], args.prefix.expanduser().resolve())
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f'install: {exc}\n')


if __name__ == '__main__':
    main()
