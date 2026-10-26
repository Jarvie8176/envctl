#!/usr/bin/env python3
"""Check publication metadata without printing matched private values."""
import argparse
import ipaddress
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
REVISION = re.compile(r'(?<![a-zA-Z0-9])[a-fA-F0-9]{40}(?![a-zA-Z0-9])')
GITHUB = re.compile(r'github[.]com[/:]([\w.-]+/[\w.-]+)')
REFERENCE = re.compile(r'(?<![\w/])([\w.-]+/[\w.-]+)(?:[#][0-9]+|@(?=[a-f0-9]{40}\b))')
EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
IDENTITY = re.compile(r'(?:Session[-_]Id|Thread[-_]Id|Conversation[-_]Id)\s*[:=]', re.I)
UUID = re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b', re.I)
LOCAL_PATH = re.compile(r'/(?:home|Users)/[A-Za-z0-9_.-]+/')
VAULT = re.compile(r'o[p]://', re.I)
TAILNET = re.compile(r'\b[\w.-]+\.(?:ts[.]net|local)\b', re.I)
IP = re.compile(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])')
IPV6 = re.compile(r'\b(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F:.]+\b')
PRIVATE_NETWORKS = tuple(ipaddress.ip_network((address, prefix)) for address, prefix in (
    (10 << 24, 8), ((172 << 24) + (16 << 16), 12), ((192 << 24) + (168 << 16), 16),
    ((100 << 24) + (64 << 16), 10), ((169 << 24) + (254 << 16), 16),
    (0xfc << 120, 7), (0xfe80 << 112, 10),
))
PUBLIC_EMAIL = re.compile(r'(?:[A-Za-z0-9+_.-]+@users\.noreply\.github\.com|noreply@github\.com)\Z')


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])


def violations(text, known_commits, repositories):
    """Return rule names only: never include the matched content in reports."""
    found = set()
    for value in REVISION.findall(text):
        if value.lower() not in known_commits:
            found.add('unknown-source-revision')
    for value in GITHUB.findall(text) + REFERENCE.findall(text):
        value = value.removesuffix('.git')
        if value.casefold() not in repositories:
            found.add('unapproved-repository-reference')
    for value in EMAIL.findall(text):
        if value != "git@github.com" and not PUBLIC_EMAIL.fullmatch(value):
            found.add('non-noreply-email')
    for pattern, rule in ((IDENTITY, 'internal-session-field'), (UUID, 'session-like-uuid'),
                          (LOCAL_PATH, 'personal-absolute-path'), (VAULT, 'vault-reference'),
                          (TAILNET, 'internal-hostname')):
        if pattern.search(text):
            found.add(rule)
    for candidate in IP.findall(text) + IPV6.findall(text):
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if any(address in network for network in PRIVATE_NETWORKS):
            found.add('private-network-address')
    return sorted(found)


def forbidden_path(name):
    parts = PurePosixPath(name).parts
    return (any(part in {'.git', '.ssh', '.aws', '.env', '.env.enc'} for part in parts)
            or bool(re.search(r'(?:^|/)(?:identity[^/]*\.(?:txt|key)|keys\.txt|[^/]+\.pem)$', name)))


def scan(root, tip, artifacts=None):
    policy = json.loads((root / '.github/privacy-policy.json').read_text())
    boundary = policy['legacy_metadata_boundary']
    if not re.fullmatch(r'[0-9a-f]{40}', boundary):
        raise ValueError('invalid historical metadata boundary')
    subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor', boundary, tip],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Only commits reachable from this publication tip count as public sources.
    known = set(git(root, 'rev-list', tip).decode().splitlines())
    known.update(policy['approved_external_commits'])
    repositories = {name.casefold() for name in policy['approved_repositories']}
    findings = []

    def check(name, data):
        # Do not echo filenames either: an accidentally committed filename can be private.
        location = len(objects)
        objects.append(name)
        if forbidden_path(name):
            findings.append({'object': location, 'rule': 'private-material-path'})
        text = data.decode('utf-8')  # fail closed on unsupported binary payloads
        for line_number, line in enumerate(text.splitlines(), 1):
            for rule in violations(line, known, repositories):
                findings.append({'object': location, 'line': line_number, 'rule': rule})

    objects = []
    paths = git(root, 'ls-tree', '-rz', '--name-only', tip).decode().split('\0')
    for name in filter(None, paths):
        check(name, git(root, 'show', f'{tip}:{name}'))
    commits = git(root, 'rev-list', f'{boundary}..{tip}').decode().splitlines()
    for commit in commits:
        changed_paths = git(root, 'diff-tree', '--root', '--no-commit-id',
                            '--name-only', '-r', '-z', '-m', '--diff-filter=ACMR', commit).decode().split('\0')
        if any(forbidden_path(name) for name in filter(None, changed_paths)):
            findings.append({'commit': commit, 'rule': 'private-material-path'})
        fields = git(root, 'show', '-s', '--format=%ae%x00%ce%x00%B', commit).decode().split('\0', 2)
        for email in fields[:2]:
            if not PUBLIC_EMAIL.fullmatch(email):
                findings.append({'commit': commit, 'rule': 'non-noreply-commit-email'})
        for rule in violations(fields[2], known, repositories):
            findings.append({'commit': commit, 'rule': rule})
        # Scan additions from every new commit, even if subsequently deleted.
        diff = git(root, 'show', '--format=', '--root', '--diff-merges=first-parent',
                   '--unified=0', commit).decode()
        additions = '\n'.join(line[1:] for line in diff.splitlines()
                              if line.startswith('+') and not line.startswith('+++'))
        for rule in violations(additions, known, repositories):
            findings.append({'commit': commit, 'rule': rule})
    if artifacts is not None:
        metadata_path = artifacts / 'RELEASE.json'
        metadata = json.loads(metadata_path.read_text())
        if set(metadata) != {'version', 'commit'} or metadata['commit'] != git(root, 'rev-parse', tip).decode().strip():
            findings.append({'rule': 'invalid-release-provenance'})
        archives = list(artifacts.glob('*.tar.gz'))
        if len(archives) != 1:
            raise ValueError('expected exactly one release archive')
        for path in sorted(artifacts.iterdir()):
            if path.is_symlink() or not path.is_file():
                raise ValueError('unsupported artifact entry')
            if path == archives[0]:
                with tarfile.open(path) as archive:
                    for entry in archive.getmembers():
                        member = PurePosixPath(entry.name)
                        if not entry.isfile() or member.is_absolute() or '..' in member.parts:
                            raise ValueError('unsafe archive member')
                        check(entry.name, archive.extractfile(entry).read())
            else:
                check(path.name, path.read_bytes())
    return findings, len(objects), len(commits)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--tip', default='HEAD')
    parser.add_argument('--artifacts', type=Path)
    args = parser.parse_args()
    try:
        findings, objects, commits = scan(args.root, args.tip, args.artifacts)
    except (OSError, ValueError, KeyError, UnicodeError, subprocess.SubprocessError, tarfile.TarError):
        print('privacy: scan incomplete; check policy, full history and supported artifacts', file=sys.stderr)
        return 1
    print(json.dumps({'objects_scanned': objects, 'new_commits_scanned': commits,
                      'findings': findings}, indent=2))
    return bool(findings)


if __name__ == '__main__':
    sys.exit(main())
