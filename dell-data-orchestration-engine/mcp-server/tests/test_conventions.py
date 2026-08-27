"""Repo-wide conventions, enforced rather than documented and hoped for.

Each of these was a real defect at some point, found by a one-off sweep. A sweep only holds until
the next commit, so the ones worth keeping live here. Where a convention has a sanctioned
exception, the exception is named explicitly — an unnamed one fails, which is the point.
"""

import ast
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


#: Modules allowed to reach for a local ML runtime, and why.
#:
#: `embeddings.py` runs CLIP in-process to encode a text query. That is a standing exception, not a
#: design: it downloads `openai/clip-vit-base-patch32` at runtime against the "never download or
#: run external/local models" convention, and `torch`/`transformers` are not declared
#: dependencies. It is gated elicitation-only (`guards.elicit_or_refuse`) and the platform-side
#: replacement already exists in `rag.py`'s `embed_query`. Remove this entry when that lands.
LOCAL_ML_EXCEPTIONS = {"embeddings.py"}

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
