# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`scripts/doe_state_dir.py`: one DATALOOP_PATH directory per DOE deployment, not one shared one.

Pure filesystem logic and no dtlpy import, so this uses the same `sys.path` injection as
`test_workflow_instrumentation.py` without needing `test_live_smoke.py`'s `importlib` dance.
"""

import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from doe_state_dir import DIR_MODE, MARKER_MODE, list_aliases, provision, slugify, state_dir_for  # noqa: E402


@pytest.fixture
def loose_umask():
    """Force a permissive umask, so a permissions test cannot pass by accident.

    Under a strict umask (0o077) the directory would come out owner-only whether or not the code
    asks for it, which is exactly the false pass that would hide a regression here.
    """
    previous = os.umask(0o022)
    yield
    os.umask(previous)


def mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_slugify_lowercases_and_replaces_unsafe_characters():
    assert slugify("Private Lab") == "private-lab"
    assert slugify("  cloud  ") == "cloud"
    assert slugify("a/b:c") == "a-b-c"


def test_slugify_rejects_an_alias_with_nothing_usable():
    with pytest.raises(ValueError, match="no usable characters"):
        slugify("///")


def test_state_dir_for_is_deterministic_and_does_not_touch_the_filesystem(tmp_path):
    directory = state_dir_for("cloud", base=tmp_path)

    assert directory == tmp_path / "dataloop-cloud"
    assert not directory.exists()


def test_provision_creates_the_directory_and_records_the_gate_url(tmp_path):
    directory = provision("cloud", "https://gate.dataloop.ai/api/v1", base=tmp_path)

    assert directory == tmp_path / "dataloop-cloud"
    assert directory.is_dir()
    assert (directory / ".gate-url").read_text().strip() == "https://gate.dataloop.ai/api/v1"


def test_provision_is_idempotent_for_the_same_alias_and_gate_url(tmp_path):
    """`first == second` alone would be tautological -- both come from the pure `state_dir_for`.
    What matters is that re-provisioning leaves existing dtlpy state and the marker untouched.
    """
    first = provision("cloud", "https://gate.dataloop.ai/api/v1", base=tmp_path)
    (first / "cookie.json").write_text('{"token": "pretend"}')

    second = provision("cloud", "https://gate.dataloop.ai/api/v1", base=tmp_path)

    assert first == second
    assert first.is_dir()
    assert (first / "cookie.json").read_text() == '{"token": "pretend"}'
    assert (first / ".gate-url").read_text() == "https://gate.dataloop.ai/api/v1\n"


def test_provision_without_a_gate_url_resolves_an_existing_alias(tmp_path):
    provision("cloud", "https://gate.dataloop.ai/api/v1", base=tmp_path)

    directory = provision("cloud", None, base=tmp_path)

    assert directory == tmp_path / "dataloop-cloud"


def test_provision_warns_but_still_resolves_a_never_recorded_alias(tmp_path, capsys):
    """Calling without --gate-url before the alias has ever been provisioned is a caller mistake,
    not a fatal one: still resolve (and create) the directory, but say why the marker is missing.
    """
    directory = provision("cloud", None, base=tmp_path)

    assert directory == tmp_path / "dataloop-cloud"
    assert directory.is_dir()
    assert "no recorded gate URL" in capsys.readouterr().err


def test_provision_refuses_to_reuse_an_alias_for_a_different_deployment(tmp_path):
    """Regression target: this is the exact bug that motivated per-alias directories in the first
    place -- two deployments sharing one DATALOOP_PATH -- recreated one level up if an alias is
    silently reused. Reusing the alias must fail loudly instead.
    """
    provision("cloud", "https://gate.dataloop.ai/api/v1", base=tmp_path)

    with pytest.raises(ValueError, match="already provisioned for 'https://gate.dataloop.ai/api/v1'"):
        provision("cloud", "https://gate.example.com/api/v1", base=tmp_path)


def test_provision_trailing_slash_does_not_look_like_a_different_deployment(tmp_path):
    directory = provision("cloud", "https://gate.dataloop.ai/api/v1", base=tmp_path)

    # Must not raise: a trailing slash is not a different deployment.
    provision("cloud", "https://gate.dataloop.ai/api/v1/", base=tmp_path)

    # And the recorded marker must be unchanged, not rewritten with the slashed form.
    assert (directory / ".gate-url").read_text() == "https://gate.dataloop.ai/api/v1\n"


# --- the token's only protection ---------------------------------------------------------
#
# dtlpy writes cookie.json -- the bearer token -- into this directory with a plain open() and
# never chmods it, so the directory mode is all that stands between the token and every other
# local user. Found 0o755 in review, with a live JWT readable at 0o644 inside it.


def test_provision_creates_an_owner_only_directory(tmp_path, loose_umask):
    directory = provision("cloud", "https://gate.dataloop.ai/api/v1", base=tmp_path)

    assert mode_of(directory) == DIR_MODE
    assert mode_of(directory) & 0o077 == 0, "the token in here would be readable by other users"
    assert mode_of(directory / ".gate-url") == MARKER_MODE


def test_provision_tightens_an_already_loose_directory(tmp_path, loose_umask):
    """The common case: the directory already exists from before this was fixed. mkdir(mode=) is
    ignored for an existing directory, so only an explicit chmod repairs it.
    """
    stale = tmp_path / "dataloop-cloud"
    stale.mkdir(parents=True)
    stale.chmod(0o755)

    provision("cloud", "https://gate.dataloop.ai/api/v1", base=tmp_path)

    assert mode_of(stale) == DIR_MODE


def test_provision_tightens_the_base_directory_too(tmp_path, loose_umask):
    base = tmp_path / "nested" / "base"

    provision("cloud", "https://gate.dataloop.ai/api/v1", base=base)

    assert mode_of(base) == DIR_MODE


def test_provision_refuses_a_symlinked_state_directory(tmp_path):
    """A symlink would put the token wherever it points while printing a path that looks normal."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "dataloop-cloud").symlink_to(elsewhere)

    with pytest.raises(ValueError, match="symlink"):
        provision("cloud", "https://gate.dataloop.ai/api/v1", base=tmp_path)


def test_provision_refuses_a_path_that_is_a_file(tmp_path):
    (tmp_path / "dataloop-cloud").write_text("not a directory")

    with pytest.raises(ValueError, match="not a directory"):
        provision("cloud", "https://gate.dataloop.ai/api/v1", base=tmp_path)


def test_list_aliases_skips_a_symlink(tmp_path):
    provision("cloud", "https://gate.dataloop.ai/api/v1", base=tmp_path)
    (tmp_path / "dataloop-impostor").symlink_to(tmp_path / "dataloop-cloud")

    assert [alias for alias, _, _ in list_aliases(tmp_path)] == ["cloud"]


def test_list_aliases_is_empty_for_a_base_that_does_not_exist_yet(tmp_path):
    assert list_aliases(tmp_path / "does-not-exist") == []


def test_list_aliases_reports_every_provisioned_alias(tmp_path):
    provision("cloud", "https://gate.dataloop.ai/api/v1", base=tmp_path)
    provision("privatelab", "https://privatelab-gate.example.net/api/v1", base=tmp_path)

    rows = list_aliases(tmp_path)

    assert [alias for alias, _, _ in rows] == ["cloud", "privatelab"]
    assert dict((alias, gate_url) for alias, _, gate_url in rows) == {
        "cloud": "https://gate.dataloop.ai/api/v1",
        "privatelab": "https://privatelab-gate.example.net/api/v1",
    }


def test_list_aliases_ignores_directories_that_are_not_its_own(tmp_path):
    (tmp_path / "unrelated").mkdir()
    provision("cloud", "https://gate.dataloop.ai/api/v1", base=tmp_path)

    rows = list_aliases(tmp_path)

    assert [alias for alias, _, _ in rows] == ["cloud"]
