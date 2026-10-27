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
import stat
import tempfile


VERSION_PATTERN = r'[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.]+)?'


def regular_file(root, name):
    path = Path(name)
    if (not name or path.is_absolute() or '..' in path.parts or path.as_posix() != name):
        raise ValueError('unsafe manifest entry')
    current = root
    for index, part in enumerate(path.parts):
        current = current / part
        mode = current.lstat().st_mode
        expected = stat.S_ISREG if index == len(path.parts) - 1 else stat.S_ISDIR
        if not expected(mode):
            raise ValueError('release entries must be regular files in real directories')
    if not path.parts:
        raise ValueError('unsafe manifest entry')
    return current


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def read_json(root, name):
    return json.loads(regular_file(root, name).read_text(), object_pairs_hook=unique_object)


def verify(root):
    manifest = read_json(root, 'MANIFEST.json')
    if not isinstance(manifest, dict):
        raise ValueError('release manifest must be an object')
    for name, digest in manifest.items():
        if (name == 'MANIFEST.json' or not isinstance(digest, str)
                or not re.fullmatch(r'[0-9a-f]{64}', digest)):
            raise ValueError('invalid manifest entry')
        path = regular_file(root, name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('release checksum mismatch: ' + name)
    for required in ('bin/envctl', 'bin/bakctl', 'RELEASE.json', 'LICENSE'):
        if required not in manifest:
            raise ValueError('incomplete release manifest')
    metadata = read_json(root, 'RELEASE.json')
    if (not isinstance(metadata, dict) or not isinstance(metadata.get('version'), str)
            or not re.fullmatch(VERSION_PATTERN, metadata['version'])):
        raise ValueError('invalid release version')
    if (not isinstance(metadata.get('commit'), str)
            or not re.fullmatch(r'[0-9a-f]{40}', metadata['commit'])):
        raise ValueError('invalid release commit')
    return metadata, manifest


def real_directory(path):
    path.mkdir(exist_ok=True)
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise ValueError('refusing linked installation directory: ' + str(path))


def activate(root, prefix):
    metadata, manifest = verify(root)
    prefix.mkdir(parents=True, exist_ok=True)
    base = prefix / 'lib/envctl'
    bindir = prefix / 'bin'
    for directory in (prefix / 'lib', base, bindir):
        real_directory(directory)
    fd = os.open(base / '.install.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'r+') as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('installation lock must be a regular, unlinked file')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('another installation is in progress') from None
        # Refuse unmanaged state before changing either command or the active version.
        current = base / 'current'
        if os.path.lexists(current):
            if not current.is_symlink() or not re.fullmatch(VERSION_PATTERN, os.readlink(current)):
                raise ValueError('refusing to replace unmanaged current entry')
            if not stat.S_ISDIR((base / os.readlink(current)).lstat().st_mode):
                raise ValueError('active release must be a real directory')
        for name in ('envctl', 'bakctl'):
            link = bindir / name
            target = str(base / 'current/bin' / name)
            if os.path.lexists(link) and (not link.is_symlink() or os.readlink(link) != target):
                raise ValueError('refusing to replace unmanaged command: ' + str(link))
        dest = base / metadata['version']
        if os.path.lexists(dest):
            if not stat.S_ISDIR(dest.lstat().st_mode) or verify(dest) != (metadata, manifest):
                raise ValueError('version already installed with different contents')
            for name in ('envctl', 'bakctl'):
                if not os.access(dest / 'bin' / name, os.X_OK):
                    raise ValueError('installed command is not executable: ' + name)
        else:
            staging = Path(tempfile.mkdtemp(prefix='.install-', dir=base))
            try:
                for name in (*manifest, 'MANIFEST.json'):
                    output = staging / name
                    output.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(root / name, output)
                if verify(staging) != (metadata, manifest):
                    raise ValueError('release changed during installation')
                for name in ('envctl', 'bakctl'):
                    (staging / 'bin' / name).chmod(0o755)
                staging.rename(dest)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        created_links = []
        temp = None
        try:
            for name in ('envctl', 'bakctl'):
                link = bindir / name
                if not os.path.lexists(link):
                    link.symlink_to(base / 'current/bin' / name)
                    created_links.append(link)
            fd, temp = tempfile.mkstemp(prefix='.current-', dir=base)
            os.close(fd)
            os.unlink(temp)
            os.symlink(metadata['version'], temp)
            os.replace(temp, current)
        except OSError:
            for link in created_links:
                link.unlink()
            raise
        finally:
            if temp is not None and os.path.lexists(temp):
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
