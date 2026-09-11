"""Deployment-specific DATALOOP_PATH helper."""

import os
import stat
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from dataloop_state_dir import (  # noqa: E402
    DIR_MODE,
    MARKER_MODE,
    list_aliases,
    provision,
    slugify,
)


def mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_alias_is_safe_and_deterministic(tmp_path):
    assert slugify("CTO Lab") == "cto-lab"
    assert provision("CTO Lab", None, base=tmp_path) == (tmp_path / "dataloop-cto-lab")
    with pytest.raises(ValueError, match="no usable"):
        slugify("///")


def test_provision_records_gate_and_repairs_permissions(tmp_path):
    directory = tmp_path / "dataloop-cloud"
    directory.mkdir()
    directory.chmod(0o755)

    resolved = provision("cloud", "https://gate.dataloop.ai/api/v1", base=tmp_path)

    assert resolved == directory
    assert (directory / ".gate-url").read_text(encoding="utf-8").strip() == (
        "https://gate.dataloop.ai/api/v1"
    )
    if os.name == "posix":
        assert mode_of(directory) == DIR_MODE
        assert mode_of(directory / ".gate-url") == MARKER_MODE


def test_alias_cannot_be_reused_for_another_gate(tmp_path):
    provision("cloud", "https://gate.one/api/v1", base=tmp_path)
    with pytest.raises(ValueError, match="already provisioned"):
        provision("cloud", "https://gate.two/api/v1", base=tmp_path)


def test_symlink_is_refused_and_listing_is_bounded(tmp_path):
    provision("cloud", "https://gate.one/api/v1", base=tmp_path)
    (tmp_path / "dataloop-link").symlink_to(tmp_path / "dataloop-cloud")
    with pytest.raises(ValueError, match="symlink"):
        provision("link", "https://gate.one/api/v1", base=tmp_path)
    assert [alias for alias, _, _ in list_aliases(tmp_path)] == ["cloud"]


def test_custom_existing_base_permissions_are_not_changed(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o755)

    directory = provision("cloud", "https://gate.one/api/v1", base=shared)

    if os.name == "posix":
        assert mode_of(shared) == 0o755
        assert mode_of(directory) == DIR_MODE
