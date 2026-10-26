"""Tests for envctl — plaintext dotenv path (no SOPS fixtures)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parent.parent / "bin"
ENVCTL = str(TOOLS_DIR / "envctl")


@pytest.fixture()
def envctl_mod(request):
    """Import envctl as a module."""
    import importlib.machinery
    import importlib.util

    loader = importlib.machinery.SourceFileLoader("envctl", ENVCTL)
    spec = importlib.util.spec_from_loader("envctl", loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["envctl"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def env_file(tmp_path):
    f = tmp_path / "test.env"
    f.write_text("A=1\nB=hello\n# comment\n\nC=world\n")
    return f


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

class TestIsEnc:
    def test_plain(self, envctl_mod):
        assert envctl_mod._is_enc(Path("/tmp/test.env")) is False

    def test_enc(self, envctl_mod):
        assert envctl_mod._is_enc(Path("/tmp/test.env.enc")) is True


class TestFind:
    def test_finds_key(self, envctl_mod):
        lines = ["A=1", "B=2", "C=3"]
        assert envctl_mod._find(lines, "B") == 1

    def test_missing_key(self, envctl_mod):
        assert envctl_mod._find(["A=1"], "Z") is None

    def test_partial_match_rejected(self, envctl_mod):
        assert envctl_mod._find(["AB=1"], "A") is None


class TestKv:
    def test_parses_pairs(self, envctl_mod):
        lines = ["A=1", "B=hello world"]
        result = list(envctl_mod._kv(lines))
        assert result == [("A", "1"), ("B", "hello world")]

    def test_skips_comments(self, envctl_mod):
        assert list(envctl_mod._kv(["# comment"])) == []

    def test_skips_blanks(self, envctl_mod):
        assert list(envctl_mod._kv(["", "  "])) == []

    def test_skips_no_equals(self, envctl_mod):
        assert list(envctl_mod._kv(["NOEQUALS"])) == []

    def test_value_with_equals(self, envctl_mod):
        result = list(envctl_mod._kv(["DB_URL=postgres://u:p@h/db?opt=1"]))
        assert result == [("DB_URL", "postgres://u:p@h/db?opt=1")]


class TestLoadWrite:
    def test_load_plaintext(self, envctl_mod, env_file):
        lines = envctl_mod._load(env_file)
        assert "A=1" in lines
        assert "B=hello" in lines

    def test_load_missing_file(self, envctl_mod, tmp_path):
        assert envctl_mod._load(tmp_path / "nonexistent") == []

    def test_write_plaintext_roundtrip(self, envctl_mod, tmp_path):
        f = tmp_path / "out.env"
        lines = ["X=1", "Y=2"]
        envctl_mod._write(f, lines)
        assert f.read_text() == "X=1\nY=2\n"
        loaded = envctl_mod._load(f)
        assert loaded == lines

    def test_write_empty(self, envctl_mod, tmp_path):
        f = tmp_path / "empty.env"
        envctl_mod._write(f, [])
        assert f.read_text() == ""

    def test_write_atomic(self, envctl_mod, tmp_path):
        f = tmp_path / "atomic.env"
        f.write_text("old\n")
        envctl_mod._write(f, ["new=val"])
        assert f.read_text() == "new=val\n"
        tmps = list(tmp_path.glob("atomic.env.*"))
        assert tmps == []


# ---------------------------------------------------------------------------
# CLI integration (subprocess)
# ---------------------------------------------------------------------------

def _run_envctl(*args: str, input: str | None = None,
                env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, ENVCTL, *args],
        capture_output=True, text=True, input=input, env=env,
    )


class TestCliGet:
    def test_get_existing(self, env_file):
        r = _run_envctl("--file", str(env_file), "get", "B")
        assert r.returncode == 0
        assert r.stdout.strip() == "hello"

    def test_get_missing(self, env_file):
        r = _run_envctl("--file", str(env_file), "get", "MISSING")
        assert r.returncode == 1


class TestCliList:
    def test_lists_keys(self, env_file):
        r = _run_envctl("--file", str(env_file), "list")
        assert r.returncode == 0
        keys = r.stdout.strip().split("\n")
        assert "A" in keys
        assert "B" in keys
        assert "C" in keys


class TestCliExport:
    def test_export_format(self, env_file):
        r = _run_envctl("--file", str(env_file), "export")
        assert r.returncode == 0
        assert "export A=" in r.stdout
        assert "export B=" in r.stdout


class TestCliSet:
    def test_set_new_key(self, env_file):
        r = _run_envctl("--file", str(env_file), "set", "NEW", "val")
        assert r.returncode == 0
        r2 = _run_envctl("--file", str(env_file), "get", "NEW")
        assert r2.stdout.strip() == "val"

    def test_set_upsert(self, env_file):
        _run_envctl("--file", str(env_file), "set", "A", "updated")
        r = _run_envctl("--file", str(env_file), "get", "A")
        assert r.stdout.strip() == "updated"

    def test_set_idempotent(self, env_file):
        r = _run_envctl("--file", str(env_file), "set", "A", "1")
        assert "no-op" in r.stderr

    def test_set_creates_backup(self, env_file):
        _run_envctl("--file", str(env_file), "set", "X", "new")
        baks = list(env_file.parent.glob(f"{env_file.name}.bak.*"))
        assert len(baks) >= 1

    def test_set_on_stale_file_keeps_reported_backup(self, env_file, tmp_path):
        """Regression: the pre-write snapshot named in envctl's output must exist,
        even when the file had not been modified for weeks."""
        import os
        import re
        import time
        stale = time.time() - 20 * 86400
        os.utime(env_file, (stale, stale))
        before = env_file.read_text()
        # HOME → tmp so bakctl uses default retention, not the caller's config
        r = _run_envctl("--file", str(env_file), "set", "X", "new",
                        env={**os.environ, "HOME": str(tmp_path)})
        assert r.returncode == 0
        m = re.search(r"\(backup: (\S+)\)", r.stderr)
        assert m, r.stderr
        bak = env_file.parent / m.group(1)
        assert bak.is_file()
        assert bak.read_text() == before

    def test_set_after_suffix_hole_keeps_reported_backup(self, env_file, tmp_path):
        """Regression: an existing suffix ahead of the clock (left by a
        same-second burst) must not make the new pre-write snapshot sort below
        it and get pruned under max_count: 1."""
        import os
        import re
        import time
        home = tmp_path / "home"
        (home / ".config" / "bakctl").mkdir(parents=True)
        (home / ".config" / "bakctl" / "config.yaml").write_text("max_count: 1\n")
        ahead = env_file.with_name(f"{env_file.name}.bak.{int(time.time()) + 100}")
        ahead.write_text("older snapshot\n")
        before = env_file.read_text()
        r = _run_envctl("--file", str(env_file), "set", "X", "new",
                        env={**os.environ, "HOME": str(home)})
        assert r.returncode == 0
        m = re.search(r"\(backup: (\S+)\)", r.stderr)
        assert m, r.stderr
        bak = env_file.parent / m.group(1)
        assert bak.is_file()
        assert bak.read_text() == before

    def test_set_from_stdin(self, env_file):
        r = _run_envctl("--file", str(env_file), "set", "SECRET", input="s3cret")
        assert r.returncode == 0
        r2 = _run_envctl("--file", str(env_file), "get", "SECRET")
        assert r2.stdout.strip() == "s3cret"


class TestCliUnset:
    def test_unset_existing(self, env_file):
        _run_envctl("--file", str(env_file), "unset", "B")
        r = _run_envctl("--file", str(env_file), "get", "B")
        assert r.returncode == 1

    def test_unset_missing_noop(self, env_file):
        r = _run_envctl("--file", str(env_file), "unset", "MISSING")
        assert r.returncode == 0
        assert "no-op" in r.stderr

    def test_unset_creates_backup(self, env_file):
        _run_envctl("--file", str(env_file), "unset", "A")
        baks = list(env_file.parent.glob(f"{env_file.name}.bak.*"))
        assert len(baks) >= 1
