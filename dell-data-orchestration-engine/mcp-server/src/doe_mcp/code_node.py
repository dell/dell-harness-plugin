# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Build a pipeline CODE node from source text.

``dl.CodeNode`` cannot be used here. It takes a ``Callable`` and recovers the source with
``inspect.getsourcelines``, so a caller must already hold a real function object defined in
a real file. An MCP client sends code as *text*, and the obvious workaround of ``exec``-ing
that text does not help: a function built that way has no source file, so
``getsourcelines`` raises ``OSError``. Executing model-authored code inside this server
would also be a needless risk, given the code is only ever meant to run on DOE's
runners.

So the node payload is assembled from the string directly. The output is byte-for-byte what
``dl.CodeNode`` produces for the equivalent function: the supplied function becomes a
``@staticmethod`` of the ``ServiceRunner`` class in dtlpy's ``main_partial`` asset.
"""

import ast
import textwrap
import uuid

import dtlpy as dl
from dtlpy import assets

INDENT = "    "


def _main_partial() -> str:
    with open(assets.paths.PARTIAL_MAIN_FILEPATH) as handle:
        return handle.read()


#: Upper bound on a code node's source. Generous next to the templates in ``tools/rag.py`` (the
#: largest is well under 4k) but small enough that a runaway generation fails fast and locally.
MAX_CODE_CHARS = 20_000


def function_name_of(source: str) -> str:
    """Validate the source and return the name of the function it defines.

    Parsing with ``ast`` checks syntax and finds the name without running anything.
    Exactly one top-level function is required: everything is indented into the
    ``ServiceRunner`` class body, so stray module-level statements would land somewhere
    they do not belong. Imports and helpers go inside the function.

    The length cap lives here rather than as a pydantic ``max_length`` on ``CodeNodeSpec.code``
    for two reasons: ``pipeline_node_update`` takes ``code`` as a plain argument and would
    otherwise skip it, and a ``maxLength`` keyword would land in the advertised JSON Schema,
    which the tool-surface budget has no room for.
    """
    if len(source) > MAX_CODE_CHARS:
        raise ValueError(
            f"The code is longer than {MAX_CODE_CHARS} characters. A pipeline code node is meant "
            "to be a single small function; move anything larger into a package or an app."
        )
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError as exc:
        raise ValueError(f"The code is not valid Python: {exc}") from exc
    except (ValueError, RecursionError, MemoryError) as exc:
        # ast.parse does not only raise SyntaxError: a null byte gives ValueError, and a
        # pathological but syntactically legal shape ("not " * 10000) exhausts the parser with
        # MemoryError. Callers are promised ValueError, so translate rather than leak either.
        raise ValueError(f"The code is too complex to parse ({type(exc).__name__}).") from exc

    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)]
    if len(functions) != 1 or len(tree.body) != 1:
        raise ValueError(
            "The code must consist of exactly one top-level function definition and nothing "
            "else. Put any imports or helpers inside that function."
        )
    return functions[0].name


def build_runner_code(source: str) -> str:
    """Wrap a function's source in dtlpy's ServiceRunner scaffold."""
    body = textwrap.dedent(source).strip("\n") + "\n"
    indented = "".join((INDENT + line if line.strip() else line) for line in body.splitlines(keepends=True))
    return f"\n{_main_partial()}\n{INDENT}@staticmethod\n{indented}"


def default_item_io() -> dl.entities.PipelineNodeIO:
    """The single-item input/output port dtlpy gives a code node by default."""
    return dl.entities.PipelineNodeIO(
        port_id=str(uuid.uuid4()),
        input_type=dl.entities.PackageInputType.ITEM,
        name="item",
        color=None,
        display_name="item",
        actions=None,
    )


def build_code_node(
    name: str,
    project_id: str,
    project_name: str,
    source: str,
    position: tuple[int, int] = (1, 1),
) -> dl.entities.node.PipelineNode:
    """Create a CODE pipeline node whose function body is ``source``.

    Must use ``dl.entities.node.PipelineNode`` (fully qualified), never ``dl.entities.PipelineNode``
    or ``dl.PipelineNode``: dtlpy 1.125.6 has two unrelated classes named ``PipelineNode`` — the
    graph node in ``dtlpy.entities.node`` (what ``dl.CodeNode``/``dl.DatasetNode`` subclass) and an
    unrelated DPK-listing entity in ``dtlpy.entities.dpk`` — and the package's public surface
    (``dl.PipelineNode`` and ``dl.entities.PipelineNode``) resolves to the *latter*. The DPK entity
    silently accepts every constructor kwarg as a plain attribute (so ``node.node_type``,
    ``node.namespace`` etc. all appear to work) but its ``to_json()`` serializes only its own
    declared fields, so the platform receives ``{"computeConfig": null}`` and rejects it with
    ``Cannot read properties of undefined (reading 'functionName')``. Verified against a live
    instance; pinned by a test asserting the fully-qualified class round-trips through ``to_json``.
    """
    function_name = function_name_of(source)
    node = dl.entities.node.PipelineNode(
        name=name,
        node_id=str(uuid.uuid4()),
        outputs=[default_item_io()],
        inputs=[default_item_io()],
        metadata={},
        node_type=dl.entities.PipelineNodeType.CODE,
        namespace=dl.entities.PipelineNameSpace(
            function_name=function_name,
            project_name=project_name,
        ),
        project_id=project_id,
        position=position,
    )
    node.config = {
        "package": {
            "code": build_runner_code(source),
            "name": function_name,
            "type": "code",
        }
    }
    return node
