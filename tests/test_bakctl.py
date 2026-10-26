"""Tests for bakctl — tiered retention, config, backup/prune."""
from __future__ import annotations

import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# _parse_duration
# ---------------------------------------------------------------------------

class TestParseDuration:
    def test_hours(self, bakctl_mod):
        assert bakctl_mod._parse_duration("24h") == 86400

    def test_days(self, bakctl_mod):
        assert bakctl_mod._parse_duration("7d") == 604800

    def test_weeks(self, bakctl_mod):
        assert bakctl_mod._parse_duration("2w") == 1209600

    def test_bare_seconds(self, bakctl_mod):
        assert bakctl_mod._parse_duration("3600") == 3600

    def test_whitespace_stripped(self, bakctl_mod):
        assert bakctl_mod._parse_duration("  12h  ") == 43200


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

class TestConfigHelpers:
    def test_deep_merge_simple(self, bakctl_mod):
        base = {"a": 1, "b": {"x": 10}}
        override = {"b": {"y": 20}, "c": 3}
        result = bakctl_mod._deep_merge(base, override)
        assert result == {"a": 1, "b": {"x": 10, "y": 20}, "c": 3}

    def test_deep_merge_override_scalar(self, bakctl_mod):
        base = {"a": 1}
        override = {"a": 2}
        assert bakctl_mod._deep_merge(base, override)["a"] == 2

    def test_get_nested(self, bakctl_mod):
        d = {"retention": {"daily": "7d"}}
        assert bakctl_mod._get_nested(d, "retention.daily") == "7d"

    def test_get_nested_missing(self, bakctl_mod):
        assert bakctl_mod._get_nested({"a": 1}, "b.c") is None

    def test_set_nested_creates_path(self, bakctl_mod):
        d = {}
        bakctl_mod._set_nested(d, "retention.daily", "3d")
        assert d == {"retention": {"daily": "3d"}}

    def test_del_nested(self, bakctl_mod):
        d = {"retention": {"daily": "7d", "weekly": "14d"}}
        assert bakctl_mod._del_nested(d, "retention.weekly") is True
        assert d == {"retention": {"daily": "7d"}}

    def test_del_nested_missing(self, bakctl_mod):
        assert bakctl_mod._del_nested({}, "a.b") is False

    def test_coerce_bool(self, bakctl_mod):
        assert bakctl_mod._coerce("true") is True
        assert bakctl_mod._coerce("False") is False

    def test_coerce_int(self, bakctl_mod):
        assert bakctl_mod._coerce("30") == 30

    def test_coerce_string(self, bakctl_mod):
        assert bakctl_mod._coerce("24h") == "24h"


class TestConfigLoadSave:
    def test_no_config_returns_empty(self, bakctl_mod, tmp_config):
        assert bakctl_mod._load_config() == {}

    def test_effective_uses_defaults(self, bakctl_mod, tmp_config):
        eff = bakctl_mod._effective_config()
        assert eff["auto_prune"] is True
        assert eff["retention"]["recent"] == "24h"
        assert eff["max_count"] == 30

    def test_save_and_load_roundtrip(self, bakctl_mod, tmp_config):
        bakctl_mod._save_config({"max_count": 10})
        cfg = bakctl_mod._load_config()
        assert cfg["max_count"] == 10

    def test_override_merges_with_defaults(self, bakctl_mod, tmp_config):
        bakctl_mod._save_config({"retention": {"daily": "3d"}})
        eff = bakctl_mod._effective_config()
        assert eff["retention"]["daily"] == "3d"
        assert eff["retention"]["recent"] == "24h"  # default preserved


# ---------------------------------------------------------------------------
# Backup core
# ---------------------------------------------------------------------------

class TestBackup:
    def test_creates_backup_file(self, bakctl_mod, tmp_file, tmp_config):
        bak = bakctl_mod.do_backup(tmp_file, auto_prune=False)
        assert bak.is_file()
        assert ".bak." in bak.name
        assert bak.read_text() == tmp_file.read_text()

    def test_backup_increments_ts_on_collision(self, bakctl_mod, tmp_file, tmp_config):
        b1 = bakctl_mod.do_backup(tmp_file, auto_prune=False)
        b2 = bakctl_mod.do_backup(tmp_file, auto_prune=False)
        assert b1 != b2
        ts1 = int(b1.name.rsplit(".bak.", 1)[-1])
        ts2 = int(b2.name.rsplit(".bak.", 1)[-1])
        assert ts2 >= ts1

    def test_suffix_above_existing_after_prune_hole(self, bakctl_mod, tmp_file,
                                                    tmp_config, monkeypatch):
        """Regression: same-second backups bump the suffix to T+1; once
        prune deletes .bak.T, a "first unused suffix" allocator reuses T, the
        new snapshot sorts below T+1, and prune deletes it on creation."""
        bakctl_mod._save_config({"max_count": 1})
        monkeypatch.setattr(bakctl_mod.time, "time", lambda: 1_800_000_000.0)
        for i in range(1, 6):
            tmp_file.write_text(f"REV={i}\n")
            bak = bakctl_mod.do_backup(tmp_file)
            assert bak.is_file(), f"backup #{i} deleted on creation"
            assert bak.read_text() == f"REV={i}\n"

    def test_burst_beyond_max_count_keeps_each_new_snapshot(self, bakctl_mod, tmp_file,
                                                            tmp_config, monkeypatch):
        monkeypatch.setattr(bakctl_mod.time, "time", lambda: 1_800_000_000.0)
        for i in range(1, 41):  # default max_count = 30
            tmp_file.write_text(f"REV={i}\n")
            bak = bakctl_mod.do_backup(tmp_file)
            assert bak.is_file(), f"backup #{i} deleted on creation"
            assert bak.read_text() == f"REV={i}\n"

    def test_suffix_above_future_existing(self, bakctl_mod, tmp_file, tmp_config):
        ahead = int(time.time()) + 100
        bakctl_mod.backup_path(tmp_file, ahead).write_text("old")
        bak = bakctl_mod.do_backup(tmp_file, auto_prune=False)
        assert bakctl_mod._backup_ts(bak) == ahead + 1

    def test_backup_nonexistent_file_exits(self, bakctl_mod, tmp_path, tmp_config):
        with pytest.raises(SystemExit):
            bakctl_mod.do_backup(tmp_path / "nonexistent")

    def test_backup_preserves_mtime(self, bakctl_mod, tmp_file, tmp_config):
        import os
        os.utime(tmp_file, (1000000, 1000000))
        bak = bakctl_mod.do_backup(tmp_file, auto_prune=False)
        assert abs(bak.stat().st_mtime - 1000000) < 2


class TestListBackups:
    def test_lists_backups_newest_first(self, bakctl_mod, tmp_file, tmp_config):
        b1 = _make_backup(tmp_file, bakctl_mod, 2000)
        b2 = _make_backup(tmp_file, bakctl_mod, 1000)
        result = bakctl_mod.list_backups(tmp_file)
        assert result[0] == b2
        assert result[1] == b1

    def test_order_follows_suffix_not_mtime(self, bakctl_mod, tmp_file, tmp_config):
        import os
        older = _make_backup(tmp_file, bakctl_mod, 2000)
        newer = _make_backup(tmp_file, bakctl_mod, 1000)
        os.utime(older, (time.time(), time.time()))   # mtime says "newest"
        os.utime(newer, (1000, 1000))                 # mtime says "ancient"
        assert bakctl_mod.list_backups(tmp_file) == [newer, older]

    def test_ignores_non_numeric_suffix(self, bakctl_mod, tmp_file, tmp_config):
        bogus = tmp_file.with_name(f"{tmp_file.name}.bak.notanumber")
        bogus.write_text("bogus")
        bakctl_mod.do_backup(tmp_file, auto_prune=False)
        result = bakctl_mod.list_backups(tmp_file)
        assert bogus not in result

    def test_empty_when_no_backups(self, bakctl_mod, tmp_file):
        assert bakctl_mod.list_backups(tmp_file) == []


# ---------------------------------------------------------------------------
# Tiered retention
# ---------------------------------------------------------------------------

def _make_backup(tmp_file, bakctl_mod, age_seconds: float) -> Path:
    """Create a backup whose snapshot time (the .bak.<ts> suffix) is
    age_seconds ago. mtime is set to match, as cp -p of a file last written
    then would; retention must not depend on it either way."""
    import os
    import shutil
    ts = int(time.time() - age_seconds)
    bak = bakctl_mod.backup_path(tmp_file, ts)
    while bak.exists():
        ts += 1
        bak = bakctl_mod.backup_path(tmp_file, ts)
    shutil.copy2(tmp_file, bak)
    os.utime(bak, (ts, ts))
    return bak


class TestTieredPartition:
    def test_recent_keeps_all(self, bakctl_mod, tmp_file, tmp_config):
        b1 = _make_backup(tmp_file, bakctl_mod, 3600)      # 1h ago
        b2 = _make_backup(tmp_file, bakctl_mod, 7200)      # 2h ago
        b3 = _make_backup(tmp_file, bakctl_mod, 10800)     # 3h ago
        baks = bakctl_mod.list_backups(tmp_file)
        cfg = bakctl_mod._effective_config()
        keep, delete = bakctl_mod._tiered_partition(baks, cfg)
        assert len(keep) == 3
        assert len(delete) == 0

    def test_daily_keeps_one_per_day(self, bakctl_mod, tmp_file, tmp_config):
        # 2 backups on the same day, 2 days ago
        _make_backup(tmp_file, bakctl_mod, 2 * 86400)
        _make_backup(tmp_file, bakctl_mod, 2 * 86400 + 3600)
        baks = bakctl_mod.list_backups(tmp_file)
        cfg = bakctl_mod._effective_config()
        keep, delete = bakctl_mod._tiered_partition(baks, cfg)
        assert len(keep) == 1
        assert len(delete) == 1

    def test_weekly_keeps_one_per_week(self, bakctl_mod, tmp_file, tmp_config):
        # 2 backups in same week, 10 days ago
        _make_backup(tmp_file, bakctl_mod, 10 * 86400)
        _make_backup(tmp_file, bakctl_mod, 10 * 86400 + 3600)
        baks = bakctl_mod.list_backups(tmp_file)
        cfg = bakctl_mod._effective_config()
        keep, delete = bakctl_mod._tiered_partition(baks, cfg)
        assert len(keep) == 1
        assert len(delete) == 1

    def test_beyond_weekly_deleted(self, bakctl_mod, tmp_file, tmp_config):
        newest = _make_backup(tmp_file, bakctl_mod, 20 * 86400)  # 20 days ago
        older = _make_backup(tmp_file, bakctl_mod, 30 * 86400)   # 30 days ago
        baks = bakctl_mod.list_backups(tmp_file)
        cfg = bakctl_mod._effective_config()
        keep, delete = bakctl_mod._tiered_partition(baks, cfg)
        assert keep == [newest]      # newest survives regardless of tier
        assert delete == [older]

    def test_newest_always_kept(self, bakctl_mod, tmp_file, tmp_config):
        only = _make_backup(tmp_file, bakctl_mod, 365 * 86400)
        keep, delete = bakctl_mod._tiered_partition(
            bakctl_mod.list_backups(tmp_file), bakctl_mod._effective_config())
        assert keep == [only]
        assert delete == []

    def test_newest_kept_with_zero_max_count(self, bakctl_mod, tmp_file, tmp_config):
        bakctl_mod._save_config({"max_count": 0})
        newest = _make_backup(tmp_file, bakctl_mod, 60)
        _make_backup(tmp_file, bakctl_mod, 120)
        keep, delete = bakctl_mod._tiered_partition(
            bakctl_mod.list_backups(tmp_file), bakctl_mod._effective_config())
        assert keep == [newest]
        assert len(delete) == 1

    def test_age_from_suffix_not_mtime(self, bakctl_mod, tmp_file, tmp_config):
        import os
        now = time.time()
        fresh = _make_backup(tmp_file, bakctl_mod, 3600)        # 1h by suffix
        os.utime(fresh, (now - 30 * 86400,) * 2)                 # 30d by mtime
        stale = _make_backup(tmp_file, bakctl_mod, 30 * 86400)  # 30d by suffix
        os.utime(stale, (now,) * 2)                              # 0s by mtime
        keep, delete = bakctl_mod._tiered_partition(
            bakctl_mod.list_backups(tmp_file), bakctl_mod._effective_config())
        assert keep == [fresh]
        assert delete == [stale]

    def test_max_count_trims(self, bakctl_mod, tmp_file, tmp_config):
        bakctl_mod._save_config({"max_count": 2})
        for i in range(5):
            _make_backup(tmp_file, bakctl_mod, 60 * (i + 1))
        baks = bakctl_mod.list_backups(tmp_file)
        cfg = bakctl_mod._effective_config()
        keep, delete = bakctl_mod._tiered_partition(baks, cfg)
        assert len(keep) == 2
        assert len(delete) == 3

    def test_mixed_tiers(self, bakctl_mod, tmp_file, tmp_config):
        _make_backup(tmp_file, bakctl_mod, 3600)          # recent
        _make_backup(tmp_file, bakctl_mod, 3 * 86400)     # daily
        _make_backup(tmp_file, bakctl_mod, 3 * 86400 + 1) # daily same day
        _make_backup(tmp_file, bakctl_mod, 10 * 86400)    # weekly
        _make_backup(tmp_file, bakctl_mod, 20 * 86400)    # beyond
        baks = bakctl_mod.list_backups(tmp_file)
        cfg = bakctl_mod._effective_config()
        keep, delete = bakctl_mod._tiered_partition(baks, cfg)
        assert len(keep) == 3  # 1 recent + 1 daily + 1 weekly
        assert len(delete) == 2  # 1 daily dup + 1 beyond


# ---------------------------------------------------------------------------
# Prune integration
# ---------------------------------------------------------------------------

class TestPrune:
    def test_tiered_prune_deletes_old(self, bakctl_mod, tmp_file, tmp_config):
        _make_backup(tmp_file, bakctl_mod, 3600)       # keep (recent)
        _make_backup(tmp_file, bakctl_mod, 20 * 86400) # delete (beyond)
        bakctl_mod._do_tiered_prune(tmp_file)
        remaining = bakctl_mod.list_backups(tmp_file)
        assert len(remaining) == 1

    def test_auto_prune_on_backup(self, bakctl_mod, tmp_file, tmp_config):
        _make_backup(tmp_file, bakctl_mod, 20 * 86400)
        assert len(bakctl_mod.list_backups(tmp_file)) == 1
        bakctl_mod.do_backup(tmp_file, auto_prune=True)
        remaining = bakctl_mod.list_backups(tmp_file)
        # old one deleted, new one kept
        assert len(remaining) == 1
        ages = [time.time() - bakctl_mod._backup_ts(b) for b in remaining]
        assert all(a < 86400 for a in ages)

    def test_backup_of_stale_file_survives_auto_prune(self, bakctl_mod, tmp_file, tmp_config):
        """Regression: snapshotting a file untouched for longer than every retention
        window must not delete the snapshot it just made."""
        import os
        stale = time.time() - 20 * 86400
        os.utime(tmp_file, (stale, stale))
        bak = bakctl_mod.do_backup(tmp_file, auto_prune=True)
        assert bak.is_file()
        assert bakctl_mod.list_backups(tmp_file) == [bak]
        assert abs(bak.stat().st_mtime - stale) < 2   # cp -p semantics intact

    def test_auto_prune_disabled(self, bakctl_mod, tmp_file, tmp_config):
        _make_backup(tmp_file, bakctl_mod, 20 * 86400)
        bakctl_mod.do_backup(tmp_file, auto_prune=False)
        assert len(bakctl_mod.list_backups(tmp_file)) == 2
