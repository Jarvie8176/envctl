"""Shared fixtures for bakctl / envctl tests."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parent.parent / "bin"


def _import_tool(name: str):
    """Import a shebang script as a module (no .py extension)."""
    path = TOOLS_DIR / name
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def bakctl_mod():
    return _import_tool("bakctl")


@pytest.fixture()
def tmp_file(tmp_path):
    """Create a temporary file with content for backup tests."""
    f = tmp_path / "test.env"
    f.write_text("KEY=value\n")
    return f


@pytest.fixture()
def tmp_config(tmp_path, monkeypatch, bakctl_mod):
    """Redirect bakctl config to a temp location."""
    cfg = tmp_path / "config.yaml"
    monkeypatch.setattr(bakctl_mod, "CONFIG_FILE", cfg)
    return cfg


@pytest.fixture(autouse=True)
def isolated_user_config(tmp_path, monkeypatch):
    """Never use the operator's backup settings in CLI subprocess tests."""
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
