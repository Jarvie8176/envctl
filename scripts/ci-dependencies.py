#!/usr/bin/env python3
"""Download checksum-pinned upstream tools into an explicit CI-only directory."""
import hashlib
import io
import os
from pathlib import Path
import platform
import sys
import tarfile
import urllib.request

PLATFORM = platform.system().lower()
ARCH = {'x86_64': 'amd64', 'arm64': 'arm64', 'aarch64': 'arm64'}[platform.machine()]
SOPS = {
    'linux-amd64': '620a9d7e3352ababeca6908cea24a6e8b14ce89a448ddbd3f94f1ef3398f470a',
    'darwin-amd64': 'dad79d1b1dea767ca38ffaa50e10330a3e807dd13c853ef9c880567acef4f1ef',
    'darwin-arm64': 'a2c0dd37eb031068af6ef213b78cfa67b7f1afd76c2e5cc404257f42bbc8367d',
}
AGE = {
    'linux-amd64': '7df45a6cc87d4da11cc03a539a7470c15b1041ab2b396af088fe9990f7c79d50',
    'darwin-amd64': '424e1d64438a730626540b2e01e98d132a64214442ca9465b3e82336d12e633e',
    'darwin-arm64': 'cf79875bd5970dc2dac60c87fa50cee1ff1f9a41b0eb273f65e174aff37c367a',
}


def download(url, expected):
    data = urllib.request.urlopen(url, timeout=60).read()
    if hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError('upstream checksum mismatch')
    return data


def main():
    dest = Path(sys.argv[1]).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    key = f'{PLATFORM}-{ARCH}'
    data = download(f'https://github.com/getsops/sops/releases/download/v3.13.1/sops-v3.13.1.{PLATFORM}.{ARCH}', SOPS[key])
    (dest / 'sops').write_bytes(data)
    archive = download(f'https://github.com/FiloSottile/age/releases/download/v1.2.1/age-v1.2.1-{key}.tar.gz', AGE[key])
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as tar:
        for name in ('age', 'age-keygen'):
            member = tar.getmember('age/' + name)
            if not member.isfile():
                raise RuntimeError('unexpected upstream archive member')
            (dest / name).write_bytes(tar.extractfile(member).read())
    for name in ('sops', 'age', 'age-keygen'):
        (dest / name).chmod(0o755)
    if 'GITHUB_PATH' in os.environ:
        with open(os.environ['GITHUB_PATH'], 'a') as stream:
            stream.write(str(dest) + '\n')


if __name__ == '__main__':
    main()
