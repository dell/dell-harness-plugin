# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""DOE credential resolution.

Authentication is API-key only: ``DATALOOP_API_KEY`` must hold a valid DOE API key.
There is deliberately no fallback to an interactive ``dlp login`` session so that the
identity a tool call runs as is never ambiguous.

``dl.login_api_key`` persists the token into dtlpy's state directory, which defaults to
``~/.dataloop`` and is shared with the user's own CLI session. Set ``DATALOOP_PATH`` to a
server-private directory to keep the two apart; dtlpy reads it at import time, so it must
be set in the environment before this process starts (see README).

``DATALOOP_PATH`` is a directory, not a URL. The platform a tool call reaches is a separate
setting, kept in dtlpy's cookie inside that directory and defaulting to
``https://gate.dataloop.ai/api/v1``. Set ``DATALOOP_GATE_URL`` to talk to another
deployment; ``DATALOOP_VERIFY_SSL=false`` covers lab instances with a self-signed chain.
"""

import logging
import os
import re
import threading

import dtlpy as dl
from mcp import MCPError
from mcp.types import INVALID_REQUEST

logger = logging.getLogger(__name__)

API_KEY_ENV_VAR = "DATALOOP_API_KEY"
STATE_DIR_ENV_VAR = "DATALOOP_PATH"
GATE_URL_ENV_VAR = "DATALOOP_GATE_URL"
VERIFY_SSL_ENV_VAR = "DATALOOP_VERIFY_SSL"

# dtlpy identifies an environment by its API base, e.g. https://gate.dataloop.ai/api/v1.
_API_BASE_SUFFIX = re.compile(r"/api/v\d+$")
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})

# dtlpy's ApiClient is a process-wide singleton and its token setter rewrites a file on
# disk, so logins are serialized even though tool bodies run in worker threads.
_lock = threading.Lock()

STATE_DIR_MODE = 0o700
TOKEN_FILE_MODE = 0o600
COOKIE_FILE_NAME = "cookie.json"


class MissingCredentials(MCPError):
    """Raised when the server has no usable DOE credentials.

    A protocol error rather than a tool error: no amount of retrying by the model can
    supply a missing API key or state directory, so the host application is the right
    audience.
    """

    def __init__(self, message: str) -> None:
        super().__init__(code=INVALID_REQUEST, message=message)


#: Owner-only, for the state directory and for the token file dtlpy leaves in it.
STATE_DIR_MODE = 0o700
TOKEN_FILE_MODE = 0o600

#: dtlpy's token lives here, written with a plain ``open(path, "w")``.
COOKIE_FILE_NAME = "cookie.json"


def _require_state_directory() -> str:
    state_dir = os.environ.get(STATE_DIR_ENV_VAR)
    if not state_dir:
        raise MissingCredentials(
            f"{STATE_DIR_ENV_VAR} is not set. Point it at a private dtlpy state directory "
            f"before starting the server, for example $HOME/.doe-mcp/dataloop."
        )
    if os.path.islink(state_dir):
        raise MissingCredentials(
            f"Refusing symbolic-link dtlpy state directory at {state_dir!r}; "
            "use a private directory owned by this identity."
        )
    try:
        os.makedirs(state_dir, exist_ok=True)
        if os.name == "posix":
            # mkdir(mode=...) does not repair a pre-existing loose directory.
            os.chmod(state_dir, STATE_DIR_MODE)
    except OSError as exc:
        raise MissingCredentials(
            f"Could not create or access the dtlpy state directory at {state_dir!r}: {exc}"
        ) from exc
    return state_dir


def _protect_token_file(state_dir: str) -> None:
    """Restrict the bearer-token cookie dtlpy persists after login."""
    cookie = os.path.join(state_dir, COOKIE_FILE_NAME)
    try:
        if os.path.exists(cookie) and os.name == "posix":
            os.chmod(cookie, TOKEN_FILE_MODE)
    except OSError as exc:
        # Report only the error code: the exception text carries local paths.
        logger.warning(
            "Could not restrict permissions on the DOE token file %s (errno %s).", COOKIE_FILE_NAME, exc.errno
        )


def _normalize_gate_url(raw: str) -> tuple[str, str]:
    """Turn a configured gate URL into dtlpy's ``(api_base, gate_root)`` pair."""
    url = raw.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise MissingCredentials(
            f"{GATE_URL_ENV_VAR} must be an http(s) URL, for example "
            f"https://gate.example.com/api/v1, got {raw!r}."
        )
    suffix = _API_BASE_SUFFIX.search(url)
    if suffix is None:
        return f"{url}/api/v1", url
    # Everything before the /api/vN suffix is what dtlpy calls the gate url.
    return url, url[: suffix.start()]


def _verify_ssl_setting() -> bool:
    return os.environ.get(VERIFY_SSL_ENV_VAR, "true").strip().lower() not in _FALSE_VALUES


def _configure_environment() -> None:
    """Point the dtlpy singleton at ``DATALOOP_GATE_URL`` when one is configured.

    Left unset, dtlpy keeps whatever is in its cookie, which defaults to the DOE
    cloud. ``setenv`` refuses a URL it has never seen, so the environment is registered
    first; it is registered rather than overwritten because ``add_environment`` replaces
    the whole entry and drops any other environment sharing its alias.
    """
    configured = os.environ.get(GATE_URL_ENV_VAR)
    if not configured:
        return

    api_base, gate_root = _normalize_gate_url(configured)
    verify_ssl = _verify_ssl_setting()
    client_api = dl.client_api
    environments = client_api.environments
    if api_base not in environments:
        client_api.add_environment(
            environment=api_base,
            gate_url=gate_root,
            verify_ssl=verify_ssl,
        )
    elif environments[api_base].get("verify_ssl") != verify_ssl:
        # Patch in place: a re-register would discard an existing alias and token.
        environments[api_base]["verify_ssl"] = verify_ssl
        client_api.environments = environments

    if client_api.environment != api_base:
        try:
            dl.setenv(api_base)
        except ConnectionError as exc:
            raise MissingCredentials(f"Could not select the DOE environment {api_base!r}: {exc}") from exc


def ensure_authenticated() -> None:
    """Make sure the shared dtlpy client holds a valid token."""
    state_dir = _require_state_directory()
    # Repair a cookie left by an older run even when the cached token is valid
    # and no new login occurs in this process.
    _protect_token_file(state_dir)

    api_key = os.environ.get(API_KEY_ENV_VAR)
    if not api_key:
        raise MissingCredentials(
            f"{API_KEY_ENV_VAR} is not set. Generate an API key in the DOE platform "
            f"and expose it to this server's environment."
        )

    with _lock:
        # Before the cached-token check: switching gate invalidates the token we hold.
        _configure_environment()
        client_api = dl.client_api
        if client_api.token == api_key and not client_api.token_expired():
            return
        try:
            dl.login_api_key(api_key)
        except ValueError as exc:
            # login_api_key validates against GET /users/me and raises ValueError.
            raise MissingCredentials(f"The {API_KEY_ENV_VAR} value was rejected by DOE: {exc}") from exc
        _protect_token_file(state_dir)

        client_api = dl.client_api
        if client_api.token_expired():
            # API keys are themselves JWTs with an exp claim; re-login cannot fix this.
            raise MissingCredentials(
                f"The API key in {API_KEY_ENV_VAR} has expired. Generate a new one in the DOE platform."
            )
        logger.info("Authenticated to DOE at %s", client_api.environment)
