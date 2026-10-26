"""Publication policy fixtures never contain real private identifiers."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/check-privacy.py'
spec = importlib.util.spec_from_file_location('privacy', SCRIPT)
privacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(privacy)


def rules(text):
    return privacy.violations(text, {'a' * 40}, {'public/project'})


@pytest.mark.parametrize(('text', 'expected'), [
    ('b' * 40, 'unknown-source-revision'),
    ('https://' + 'github.com/' + 'unapproved/project', 'unapproved-repository-reference'),
    ('unapproved/project' + '#123', 'unapproved-repository-reference'),
    ('someone' + '@' + 'example.org', 'non-noreply-email'),
    ('Session' + '-Id: fixture', 'internal-session-field'),
    ('12345678' + '-1234-7123-8123-' + '123456789012', 'session-like-uuid'),
    ('/home/' + 'example/private/', 'personal-absolute-path'),
    ('o' + 'p://example/item', 'vault-reference'),
    ('example' + '.ts.net', 'internal-hostname'),
    ('.'.join(['10', '20', '30', '40']), 'private-network-address'),
    ('.'.join(['100', '100', '0', '1']), 'private-network-address'),
    ('fc00' + '::1', 'private-network-address'),
    ('fe80' + '::1', 'private-network-address'),
])
def test_metadata_rules(text, expected):
    assert expected in rules(text)


def test_public_references_and_hashes_are_allowed():
    for text in ('a' * 40, 'b' * 64, 'author@users.noreply.github.com',
                 repr('author@users.noreply.github.com'),
                 'https://' + 'github.com/' + 'public/project',
                 'git' + '@github.com:' + 'public/project.git',
                 'public/project' + '#1', 'public/project@' + 'a' * 40):
        assert rules(text) == []


def test_private_material_filenames():
    assert privacy.forbidden_path('package/.env')
    assert privacy.forbidden_path('package/identity.txt')
    assert not privacy.forbidden_path('examples/example.env')


def git(root, *args, **kwargs):
    return subprocess.check_output(['git', '-C', str(root), *args], **kwargs).decode().strip()


@pytest.fixture
def repository(tmp_path):
    git(tmp_path, 'init', '-q')
    git(tmp_path, 'config', 'user.name', 'Test')
    git(tmp_path, 'config', 'user.email', 'test@users.noreply.github.com')
    git(tmp_path, 'commit', '-q', '--allow-empty', '-m', 'Public baseline')
    boundary = git(tmp_path, 'rev-parse', 'HEAD')
    (tmp_path / '.github').mkdir()
    (tmp_path / '.github/privacy-policy.json').write_text(json.dumps({
        'legacy_metadata_boundary': boundary,
        'approved_repositories': ['public/project'], 'approved_external_commits': [],
    }))
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-q', '-m', 'Add publication policy')
    return tmp_path


def test_checks_deleted_new_content_and_commit_metadata(repository):
    path = repository / '.env'
    path.write_text('z' + '@' + 'example.org')
    git(repository, 'add', '.')
    git(repository, 'commit', '-q', '-m', 'Session' + '-Id: sample')
    path.unlink()
    git(repository, 'add', '-u')
    git(repository, 'commit', '-q', '-m', 'Remove file')
    findings, _, _ = privacy.scan(repository, 'HEAD')
    assert any(f['rule'] == 'private-material-path' for f in findings)
    assert any(f['rule'] == 'internal-session-field' for f in findings)
    assert any(f['rule'] == 'non-noreply-email' for f in findings)


def test_actual_author_and_committer_email_are_checked(repository):
    git(repository, '-c', 'user.email=person' + '@' + 'example.org',
        'commit', '-q', '--allow-empty', '-m', 'Example')
    findings, _, _ = privacy.scan(repository, 'HEAD')
    assert any(f['rule'] == 'non-noreply-commit-email' for f in findings)


def test_missing_history_fails_closed_and_does_not_echo_content(repository):
    result = subprocess.run([sys.executable, str(SCRIPT), '--root', str(repository), '--tip', 'missing'],
                            text=True, capture_output=True)
    assert result.returncode == 1
    assert 'scan incomplete' in result.stderr


def test_artifact_contents_and_provenance_are_checked(repository, tmp_path):
    import io
    import tarfile
    artifacts = tmp_path / 'artifacts'
    artifacts.mkdir()
    commit = git(repository, 'rev-parse', 'HEAD')
    (artifacts / 'RELEASE.json').write_text(json.dumps({'version': '1.0.0', 'commit': commit}))
    data = ('b' * 40).encode()
    with tarfile.open(artifacts / 'package.tar.gz', 'w:gz') as tar:
        member = tarfile.TarInfo('package/NOTICE')
        member.size = len(data)
        tar.addfile(member, io.BytesIO(data))
    findings, _, _ = privacy.scan(repository, 'HEAD', artifacts)
    assert any(f['rule'] == 'unknown-source-revision' for f in findings)
    (artifacts / 'RELEASE.json').write_text(json.dumps({'version': '1.0.0', 'commit': 'b' * 40, 'private_origin': 'hidden'}))
    findings, _, _ = privacy.scan(repository, 'HEAD', artifacts)
    assert any(f['rule'] == 'invalid-release-provenance' for f in findings)


def test_unsafe_archive_rejected(repository, tmp_path):
    import tarfile
    artifacts = tmp_path / 'bad-archive'
    artifacts.mkdir()
    (artifacts / 'RELEASE.json').write_text(json.dumps({'version': '1.0.0', 'commit': git(repository, 'rev-parse', 'HEAD')}))
    with tarfile.open(artifacts / 'package.tar.gz', 'w:gz') as tar:
        member = tarfile.TarInfo('../outside')
        tar.addfile(member)
    with pytest.raises(ValueError, match='unsafe archive'):
        privacy.scan(repository, 'HEAD', artifacts)
