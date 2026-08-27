"""The code node builder must match dtlpy byte-for-byte."""

import inspect
import textwrap

import dtlpy as dl
import pytest

from doe_mcp import code_node

from . import sample_functions


@pytest.mark.parametrize("func", [sample_functions.my_handler, sample_functions.passthrough])
def test_matches_dtlpy_codenode_output(func):
    """Our string-built payload equals what dl.CodeNode builds from the callable."""
    reference = dl.CodeNode(name="n", project_id="p", project_name="proj", method=func).config["package"][
        "code"
    ]

    source = textwrap.dedent(inspect.getsource(func))

    assert code_node.build_runner_code(source) == reference


def test_exec_defined_function_cannot_be_used_by_dtlpy():
    """Why this module exists: a function from exec'd text has no source to recover.

    This is the failure an "exec the string, hand dtlpy the callable" implementation would
    hit, so it is pinned here rather than left as a claim in a comment.
    """
    namespace: dict = {}
    exec("def run(item):\n    return item\n", namespace)  # noqa: S102 - the point of the test

    with pytest.raises(OSError):
        dl.CodeNode(name="n", project_id="p", project_name="proj", method=namespace["run"])


def test_builds_node_with_code_type_and_default_ports():
    node = code_node.build_code_node(
        name="my node",
        project_id="proj-id",
        project_name="proj",
        source="def run(item):\n    return item\n",
    )

    assert node.node_type == dl.entities.PipelineNodeType.CODE
    assert node.config["package"]["name"] == "run"
    assert node.config["package"]["type"] == "code"
    assert node.namespace.function_name == "run"
    assert [io.name for io in node.inputs] == ["item"]
    assert [io.name for io in node.outputs] == ["item"]
    # Ports must be distinct, or connections cannot address them unambiguously.
    assert node.inputs[0].port_id != node.outputs[0].port_id


def test_node_is_the_graph_class_not_the_shadowing_dpk_entity():
    """Pins the actual defect: attribute access alone cannot tell these two classes apart.

    dtlpy 1.125.6 exposes two unrelated classes named ``PipelineNode``. The DPK-listing one
    (``dl.entities.PipelineNode`` / ``dl.PipelineNode``) accepts any constructor kwarg as a bare
    attribute, so a node built from it still answers ``node.node_type``, ``node.namespace`` etc.
    correctly-looking values — the assertions above alone do not catch it. Its ``to_json()``
    silently drops everything except ``computeConfig``, which the platform then rejects. This
    test asserts the class identity and the ``to_json`` contract directly, so a regression to the
    shadowed name fails here instead of only on a live pipeline call.
    """
    node = code_node.build_code_node(
        name="my node",
        project_id="proj-id",
        project_name="proj",
        source="def run(item):\n    return item\n",
    )

    assert isinstance(node, dl.entities.node.PipelineNode)
    assert not isinstance(node, dl.entities.dpk.PipelineNode)

    payload = node.to_json()
    assert payload.get("name") == "my node"
    assert payload.get("type") == "code"
    assert payload.get("namespace", {}).get("functionName") == "run"
    assert payload.get("config", {}).get("package", {}).get("name") == "run"


def test_function_name_is_extracted_without_executing():
    marker = "raise AssertionError('module level code ran')"
    with pytest.raises(ValueError, match="exactly one top-level function"):
        code_node.function_name_of(f"{marker}\n\ndef run(item):\n    return item\n")


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("def broken(:\n    pass\n", "not valid Python"),
        ("x = 1\n", "exactly one top-level function"),
        ("def a():\n    pass\n\n\ndef b():\n    pass\n", "exactly one top-level function"),
        ("", "exactly one top-level function"),
    ],
)
def test_rejects_unusable_code(source, message):
    with pytest.raises(ValueError, match=message):
        code_node.function_name_of(source)


def test_accepts_indented_source():
    """Source pasted with leading indentation still works."""
    source = "    def run(item):\n        return item\n"
    assert code_node.function_name_of(source) == "run"
    assert "    @staticmethod\n    def run(item):\n" in code_node.build_runner_code(source)


def test_rejects_source_past_the_length_cap():
    """An oversized body must fail here, not deep inside ast or on the platform.

    Enforced in `function_name_of` rather than as a pydantic `max_length` so both entry points
    (`pipeline_node_add` via CodeNodeSpec, and `pipeline_node_update`) get it, and so no
    `maxLength` lands in the advertised JSON Schema -- the tool-surface budget has no headroom.
    """
    source = "def run(item):\n" + "    x = 1\n" * code_node.MAX_CODE_CHARS

    with pytest.raises(ValueError, match="longer than"):
        code_node.function_name_of(source)


def test_parser_exhaustion_surfaces_as_the_documented_error(monkeypatch):
    """ast.parse can fail with more than SyntaxError, and callers are promised ValueError.

    Real shapes reach this: `"not " * 10000` raises MemoryError out of ast.parse on CPython 3.11,
    and a null byte raises ValueError. Deeply nested brackets and indentation happen to raise
    SyntaxError subclasses, so they were already handled -- these are not.
    """

    def exhausted(*args, **kwargs):
        raise MemoryError

    monkeypatch.setattr(code_node.ast, "parse", exhausted)

    with pytest.raises(ValueError, match="too complex to parse"):
        code_node.function_name_of("def run(item):\n    return item\n")


def test_rejects_source_containing_a_null_byte():
    """ast.parse raises ValueError, not SyntaxError, for embedded nulls."""
    with pytest.raises(ValueError, match="not valid Python"):
        code_node.function_name_of("def run(item):\n    return '\x00'\n")
