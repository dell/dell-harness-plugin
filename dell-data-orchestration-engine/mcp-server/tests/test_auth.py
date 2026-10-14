# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Credential handling."""

import os
import stat
from typing import Any

import pytest
from mcp import MCPError

from doe_mcp import auth


@pytest.fixture
def env_calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Record environment registration without letting dtlpy write a cookie file.

    ``service_defaults.DATALOOP_PATH`` is bound when dtlpy is imported, so a real
    ``add_environment`` here would write to the developer's own ``~/.dataloop``.
    """
    recorded: dict[str, Any] = {
        "added": [],
        "selected": [],
        "environments": {},
        "current": "https://gate.dataloop.ai/api/v1",
    }
    monkeypatch.setattr(
        auth.dl.client_api,
        "add_environment",
        lambda **kwargs: recorded["added"].append(kwargs),
    )
    monkeypatch.setattr(auth.dl, "setenv", lambda env: recorded["selected"].append(env))
    monkeypatch.setattr(
        type(auth.dl.client_api),
        "environments",
        property(
            lambda self: recorded["environments"],
            lambda self, value: recorded.update(environments=value),
        ),
    )
    monkeypatch.setattr(
        type(auth.dl.client_api),
        "environment",
        property(lambda self: recorded["current"]),
    )
    return recorded


def test_missing_key_raises_protocol_error(monkeypatch):
    """No key is not something a model can retry its way out of."""
    monkeypatch.delenv(auth.API_KEY_ENV_VAR, raising=False)

    with pytest.raises(auth.MissingCredentials) as excinfo:
        auth.ensure_authenticated()

    # A subclass of MCPError, so the SDK reports it as a JSON-RPC error rather than
    # feeding it back to the model as tool output.
    assert isinstance(excinfo.value, MCPError)
    assert auth.API_KEY_ENV_VAR in str(excinfo.value)


def test_missing_state_directory_is_reported(monkeypatch):
    """The server must not write into the user's shared dtlpy state directory."""
    monkeypatch.delenv(auth.STATE_DIR_ENV_VAR, raising=False)
    monkeypatch.setenv(auth.API_KEY_ENV_VAR, "any-key")

    with pytest.raises(auth.MissingCredentials, match=auth.STATE_DIR_ENV_VAR):
        auth.ensure_authenticated()


def test_state_directory_permissions_are_repaired(monkeypatch, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    state.chmod(0o755)
    monkeypatch.setenv(auth.STATE_DIR_ENV_VAR, str(state))

    assert auth._require_state_directory() == str(state)
    if os.name == "posix":
        assert stat.S_IMODE(state.stat().st_mode) == auth.STATE_DIR_MODE


def test_cookie_permissions_are_repaired(monkeypatch, tmp_path):
    cookie = tmp_path / auth.COOKIE_FILE_NAME
    cookie.write_text('{"token": "test"}', encoding="utf-8")
    cookie.chmod(0o644)

    auth._protect_token_file(str(tmp_path))

    if os.name == "posix":
        assert stat.S_IMODE(cookie.stat().st_mode) == auth.TOKEN_FILE_MODE


def test_symlinked_state_directory_is_refused(monkeypatch, tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "state"
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.setenv(auth.STATE_DIR_ENV_VAR, str(link))

    with pytest.raises(auth.MissingCredentials, match="symbolic-link"):
        auth._require_state_directory()


def test_rejected_key_is_reported_as_protocol_error(monkeypatch):
    monkeypatch.setenv(auth.API_KEY_ENV_VAR, "not-a-real-key")
    monkeypatch.setattr(auth.dl.client_api, "token", None, raising=False)

    def reject(api_key):
        raise ValueError("401 unauthorized")

    monkeypatch.setattr(auth.dl, "login_api_key", reject)

    with pytest.raises(auth.MissingCredentials, match="rejected by DOE"):
        auth.ensure_authenticated()


def test_expired_key_is_called_out(monkeypatch):
    """An expired API key cannot be fixed by logging in again, so say so plainly."""
    monkeypatch.setenv(auth.API_KEY_ENV_VAR, "expired-key")
    monkeypatch.setattr(auth.dl, "login_api_key", lambda api_key: None)
    monkeypatch.setattr(auth.dl.client_api, "token_expired", lambda *a, **k: True)
    monkeypatch.setattr(auth.dl.client_api, "_token", None, raising=False)

    with pytest.raises(auth.MissingCredentials, match="has expired"):
        auth.ensure_authenticated()


def test_valid_token_is_not_re_logged_in(monkeypatch):
    """A cached, still-valid token means no extra round trip per tool call."""
    monkeypatch.setenv(auth.API_KEY_ENV_VAR, "good-key")
    monkeypatch.setattr(auth.dl.client_api, "token_expired", lambda *a, **k: False)
    monkeypatch.setattr(type(auth.dl.client_api), "token", property(lambda self: "good-key"))

    logins = []
    monkeypatch.setattr(auth.dl, "login_api_key", lambda api_key: logins.append(api_key))

    auth.ensure_authenticated()

    assert logins == []


LAB_GATE = "https://gate.example.com"
LAB_API_BASE = f"{LAB_GATE}/api/v1"


def test_default_deployment_is_left_alone(monkeypatch, env_calls):
    """With no gate configured, dtlpy keeps whatever its cookie says."""
    monkeypatch.setenv(auth.API_KEY_ENV_VAR, "good-key")
    monkeypatch.delenv(auth.GATE_URL_ENV_VAR, raising=False)
    monkeypatch.setattr(auth.dl.client_api, "token_expired", lambda *a, **k: False)
    monkeypatch.setattr(type(auth.dl.client_api), "token", property(lambda self: "good-key"))

    auth.ensure_authenticated()

    assert env_calls["added"] == []
    assert env_calls["selected"] == []


def test_bare_gate_host_gets_the_api_base_appended(monkeypatch, env_calls):
    """dtlpy identifies an environment by its API base, not by the host alone."""
    monkeypatch.setenv(auth.GATE_URL_ENV_VAR, f"{LAB_GATE}/")

    auth._configure_environment()

    assert env_calls["added"] == [{"environment": LAB_API_BASE, "gate_url": LAB_GATE, "verify_ssl": True}]
    assert env_calls["selected"] == [LAB_API_BASE]


def test_full_api_base_yields_the_gate_root(monkeypatch, env_calls):
    monkeypatch.setenv(auth.GATE_URL_ENV_VAR, "https://gate.example.com/api/v2")

    auth._configure_environment()

    assert env_calls["added"] == [
        {"environment": "https://gate.example.com/api/v2", "gate_url": LAB_GATE, "verify_ssl": True}
    ]


def test_ssl_verification_can_be_disabled(monkeypatch, env_calls):
    """Lab deployments serve self-signed chains; dtlpy reads verify_ssl per request."""
    monkeypatch.setenv(auth.GATE_URL_ENV_VAR, LAB_API_BASE)
    monkeypatch.setenv(auth.VERIFY_SSL_ENV_VAR, "false")

    auth._configure_environment()

    assert env_calls["added"][0]["verify_ssl"] is False


def test_known_environment_is_patched_not_reregistered(monkeypatch, env_calls):
    """A re-register would drop the alias and token dtlpy already holds for this gate."""
    monkeypatch.setenv(auth.GATE_URL_ENV_VAR, LAB_API_BASE)
    monkeypatch.setenv(auth.VERIFY_SSL_ENV_VAR, "0")
    env_calls["environments"] = {LAB_API_BASE: {"alias": "lab", "token": "t", "verify_ssl": True}}

    auth._configure_environment()

    assert env_calls["added"] == []
    assert env_calls["environments"][LAB_API_BASE] == {"alias": "lab", "token": "t", "verify_ssl": False}


def test_current_environment_is_not_reselected(monkeypatch, env_calls):
    """setenv discards the cached token, so it must not run on every tool call."""
    monkeypatch.setenv(auth.GATE_URL_ENV_VAR, LAB_API_BASE)
    env_calls["environments"] = {LAB_API_BASE: {"verify_ssl": True}}
    env_calls["current"] = LAB_API_BASE

    auth._configure_environment()

    assert env_calls["selected"] == []


def test_gate_url_without_scheme_is_rejected(monkeypatch, env_calls):
    """A hostname alone is a server misconfiguration the model cannot recover from."""
    monkeypatch.setenv(auth.GATE_URL_ENV_VAR, "gate.example.com")

    with pytest.raises(auth.MissingCredentials, match=auth.GATE_URL_ENV_VAR):
        auth._configure_environment()

    assert env_calls["added"] == []
