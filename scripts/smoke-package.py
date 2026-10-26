#!/usr/bin/env python3
"""Exercise the built archive and deterministic rebuild in disposable directories."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]

with tempfile.TemporaryDirectory() as directory:
    temp = Path(directory)
    for name in ('first', 'second'):
        subprocess.run([sys.executable, str(ROOT / 'scripts/build.py'), '--output', str(temp / name)], check=True)
    first = next((temp / 'first').glob('*.tar.gz'))
    second = next((temp / 'second').glob('*.tar.gz'))
    assert first.read_bytes() == second.read_bytes(), 'non-deterministic build'
    with tarfile.open(first) as archive:
        for member in archive.getmembers():
            assert member.isfile() and not Path(member.name).is_absolute()
            assert '..' not in Path(member.name).parts
        archive.extractall(temp / 'extracted')  # verified allowlisted regular members
    release = next((temp / 'extracted').iterdir())
    for name, digest in json.loads((release / 'MANIFEST.json').read_text()).items():
        assert hashlib.sha256((release / name).read_bytes()).hexdigest() == digest
    prefix = temp / 'prefix'
    subprocess.run(['sh', str(release / 'scripts/install.sh'), '--prefix', str(prefix)], check=True)
    metadata = json.loads((release / 'RELEASE.json').read_text())
    for name in ('envctl', 'bakctl'):
        version = subprocess.check_output([str(prefix / 'bin' / name), '--version'], text=True)
        assert metadata['version'] in version and metadata['commit'] in version
    print('deterministic package, manifest and installed source versions verified')
