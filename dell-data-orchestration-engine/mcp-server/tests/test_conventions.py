# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Repo-wide conventions, enforced rather than documented and hoped for.

Each of these was a real defect at some point, found by a one-off sweep. A sweep only holds until
the next commit, so the ones worth keeping live here. Where a convention has a sanctioned
exception, the exception is named explicitly — an unnamed one fails, which is the point.
"""

import ast
import re
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "doe_mcp"
SOURCES = {path: path.read_text() for path in SRC.rglob("*.py")}


def _functions(tree: ast.AST):
    return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]


@pytest.mark.parametrize("path", sorted(SOURCES), ids=lambda p: p.name)
def test_no_function_returns_a_bare_dict_annotation(path):
    """`-> dict` produces no structured_content; it must be `dict[str, Any]` (techspec A.7)."""
    offenders = [
        node.name
        for node in _functions(ast.parse(SOURCES[path]))
        if isinstance(node.returns, ast.Name) and node.returns.id == "dict"
    ]

    assert not offenders, f"{path.name} has bare `dict` returns: {offenders}"


@pytest.mark.parametrize("path", sorted(SOURCES), ids=lambda p: p.name)
def test_no_tool_returns_an_error_dict(path):
    """Tools raise on error. A returned dict reads as success to the caller."""
    tree = ast.parse(SOURCES[path])
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Return)
        and isinstance(node.value, ast.Dict)
        and any(isinstance(k, ast.Constant) and k.value == "error" for k in node.value.keys)
    ]

    assert not offenders, f"{path.name} returns an error dict at line(s) {offenders}"


#: Modules allowed to reach for a local ML runtime. Empty by design: visual-search
#: text queries are embedded by the model deployed on Dataloop.
LOCAL_ML_EXCEPTIONS: set[str] = set()

ML_RUNTIMES = ("sklearn", "torch", "tensorflow", "transformers", "cv2")


@pytest.mark.parametrize("path", sorted(SOURCES), ids=lambda p: p.name)
def test_local_ml_runtimes_are_confined_to_named_exceptions(path):
    """A new local-inference site must be a deliberate, reviewed addition to the list above."""
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(ast.parse(SOURCES[path]))
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(ast.parse(SOURCES[path]))
        if isinstance(node, ast.ImportFrom)
    }
    found = sorted(imported & set(ML_RUNTIMES))

    if path.name in LOCAL_ML_EXCEPTIONS:
        pytest.skip(f"{path.name} is a named exception: {found or 'no runtime imported'}")
    assert not found, (
        f"{path.name} imports {found} and runs it locally. The project uses models deployed on "
        "DOE; if this is genuinely necessary, gate it and add the module to "
        "LOCAL_ML_EXCEPTIONS with a reason."
    )


@pytest.mark.parametrize("name", ["blueprint.py", "skills.py"])
def test_deterministic_graph_construction_never_mints_a_random_id(name):
    """Derived ids are what make a rebuilt graph comparable; a uuid4 anywhere here breaks that.

    Checked against the AST, not the text: both files *discuss* uuid4 in comments explaining what
    they replaced, so a substring search passes for the wrong reason.
    """
    path = next(p for p in SOURCES if p.name == name)
    calls = [
        node
        for node in ast.walk(ast.parse(SOURCES[path]))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"uuid4", "uuid1"}
    ]

    assert not calls, f"{name} mints a random id at line(s) {[c.lineno for c in calls]}"


def test_every_tool_module_exposes_a_register_function():
    """server.py calls `module.register(mcp)` unconditionally; a missing one is an import-time crash."""
    from doe_mcp.server import ALL_MODULES, PROFILE_ONLY

    for module in (*ALL_MODULES, *PROFILE_ONLY):
        assert callable(getattr(module, "register", None)), f"{module.__name__} has no register()"


#: Project-level repositories whose ``get`` returns a resource that names its own project.
PROJECT_SCOPED_REPOSITORIES = {
    "datasets",
    "pipelines",
    "models",
    "services",
    "triggers",
    "executions",
    "apps",
}

# Not checked here, by design: dataset-scoped repositories (`dataset.items`, `dataset.annotations`,
# `dataset.features`). They are reached only through a dataset that `context` has already
# verified, and item responses do not reliably carry a project identity to check again.

#: Lookups that are allowed to bypass `context`'s ownership helpers, each with its reason.
UNVERIFIED_LOOKUP_EXCEPTIONS = {
    # Looked up by name only to find an id, then re-fetched through context.get_pipeline.
    ("skills.py", "pipelines"),
}


@pytest.mark.parametrize("path", sorted(SOURCES), ids=lambda p: p.name)
def test_project_scoped_lookups_go_through_ownership_checks(path):
    """R04: a `<repo>.get(...)` on a project-level repository must go through `context`, which
    rejects a response owned by another project before any caller can use it. String literals
    (code that runs on DOE's runners) are not calls, so the AST walk ignores them.
    """
    if path.name == "context.py":
        return
    offenders = []
    for node in ast.walk(ast.parse(SOURCES[path])):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "get" or not isinstance(node.func.value, ast.Attribute):
            continue
        repository = node.func.value.attr
        if (
            repository in PROJECT_SCOPED_REPOSITORIES
            and (path.name, repository) not in UNVERIFIED_LOOKUP_EXCEPTIONS
        ):
            offenders.append(f"line {node.lineno}: .{repository}.get(...)")

    assert not offenders, f"{path.name} bypasses context ownership checks: {offenders}"


def test_every_cited_document_ships_with_the_server():
    """R08: a `docs/...md` path cited by shipped code must exist.

    Resolved against this server's root, or, when it is bundled in the plugin repository, that
    repository's root.
    """
    root = SRC.parents[1]
    bases = [root, root.parents[1]]
    cited = set()
    for path in [*SRC.rglob("*.py"), *(root / "scripts").glob("*.py")]:
        cited |= set(re.findall(r"docs/[A-Za-z0-9_./-]+\.md", path.read_text(encoding="utf-8")))
    missing = sorted(path for path in cited if not any((base / path).is_file() for base in bases))

    assert missing == []
