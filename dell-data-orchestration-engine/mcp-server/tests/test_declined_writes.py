# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Declining a gated write stops it before anything on the platform changes.

Every advertised tool with a ``confirm`` gate, plus ``rag_query`` (which uploads a temporary item),
is called through a real elicitation-capable client that declines. Each platform lookup returns a
recording fake: reads are allowed, because several tools verify ownership or preview a template
before asking, but any mutating SDK call fails the test. The elicitation callback must actually
run, which proves the gate was reached rather than the call failing for some other reason.

Write tools without a server-side gate (and a plain ``model_install``, whose ``confirm`` covers
only ``custom_installation`` overrides) rely on the host's approval prompt.
``test_tool_safety_labels`` pins that none of them is labelled read-only, which is what makes
Codex ask before running them; declining there means the tool is never called.
"""

import asyncio

import mcp_types as types
import pytest
from mcp import Client

from doe_mcp.server import build_server
from doe_mcp.tools import (
    annotations,
    datasets,
    embeddings,
    executions,
    models,
    pipeline_nodes,
    pipelines,
    rag,
    skills,
    triggers,
)

PROFILES = ["discovery", "pipelines", "models", "rag", "skills"]
MUTATING = {
    "create", "delete", "update", "upload", "install", "uninstall", "deploy", "train", "predict",
    "execute", "terminate", "pause", "reset", "embed", "embed_datasets", "add_subset",
    "remove_subset", "patch", "push", "clone", "move", "set",
}


class Mutated(AssertionError):
    pass


class Recorder:
    """Any attribute is another Recorder; calling a mutating method raises."""

    def __init__(self, path="platform", log=None):
        self._path = path
        self._log = log if log is not None else []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return Recorder(f"{self._path}.{name}", self._log)

    def __call__(self, *args, **kwargs):
        method = self._path.rsplit(".", 1)[-1]
        self._log.append(self._path)
        if method in MUTATING:
            raise Mutated(f"mutating call before the user answered: {self._path}")
        return Recorder(f"{self._path}()", self._log)

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def __bool__(self):
        return True

    def __getitem__(self, key):
        return Recorder(f"{self._path}[{key!r}]", self._log)

    def __str__(self):
        return self._path.rsplit(".", 1)[-1]

    def __eq__(self, other):
        # Ownership checks compare ids; a fake owned by the requested project passes them.
        return True

    __hash__ = object.__hash__

    # Counts read before a gate (items to embed, annotations to delete) behave as a small number.
    def __int__(self):
        return 2

    __index__ = __int__

    def __lt__(self, other):
        return 2 < other

    def __gt__(self, other):
        return 2 > other

    def __le__(self, other):
        return 2 <= other

    def __ge__(self, other):
        return 2 >= other


@pytest.fixture(autouse=True)
def recording_platform(monkeypatch):
    log: list[str] = []

    def lookup(name):
        return lambda *args, **kwargs: Recorder(name, log)

    for module in (annotations, datasets, embeddings, executions, models, pipeline_nodes,
                   pipelines, rag, skills, triggers):
        for name in ("get_project", "get_dataset", "get_pipeline", "get_execution",
                     "marketplace_dpks", "get_item", "get_annotation"):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, lookup(name))
    def owned(project, repository, kind, **lookup_):
        return Recorder(kind, log)

    for module in (models, rag, skills, triggers, embeddings):
        if hasattr(module, "owned"):
            monkeypatch.setattr(module, "owned", owned)
    # A template preview is a read: give it one valid template so the tool reaches its gate.
    monkeypatch.setattr(pipelines, "select_template", lambda templates, name: {"name": "template"})
    monkeypatch.setattr(pipelines, "template_pipeline_json", lambda **kwargs: {"name": "template"})
    monkeypatch.setattr(pipelines, "validation_passed", lambda validation: True)
    return log


# Minimal valid arguments; every other parameter keeps its default (confirm=False included).
GATED_CALLS = {
    "dataset_delete": {"dataset_id": "ds-1"},
    "annotation_delete": {"dataset_id": "ds-1", "annotation_id": "ann-1"},
    "annotation_delete_by_model": {"dataset_id": "ds-1", "model_id": "m-1"},
    "pipeline_create_from_template": {"dpk_name": "templates"},
    "pipeline_delete": {"pipeline_id": "pipe-1"},
    "pipeline_install": {"pipeline_id": "pipe-1"},
    "pipeline_node_remove": {"pipeline_id": "pipe-1", "node_name": "n"},
    "pipeline_disconnect_nodes": {"pipeline_id": "pipe-1", "source_node_name": "a", "target_node_name": "b"},
    "model_create": {"app_id": "a-1", "dpk_model_name": "d", "model_name": "m", "configuration": {"x": 1}},
    "model_deploy": {"model_id": "m-1"},
    "model_train": {"model_id": "m-1"},
    "model_predict": {"model_id": "m-1", "dataset_id": "ds-1"},
    "model_delete": {"model_id": "m-1"},
    "app_uninstall": {"app_id": "a-1"},
    "trigger_create": {"name": "t", "dataset_id": "ds-1", "pipeline_id": "pipe-1"},
    "trigger_delete": {"trigger_id": "t-1"},
    "dataset_embed": {"dataset_id": "ds-1", "model_id": "m-1"},
    "pipeline_execute": {"pipeline_id": "pipe-1"},
    "execution_terminate": {"execution_id": "e-1"},
    "rag_pipeline_build": {
        "name": "r", "source_dataset_id": "a", "output_dataset_id": "b", "model_id": "m-1"
    },
    "rag_query": {"dataset_id": "ds-1", "query": "q", "model_id": "m-1", "feature_set_id": "fs-1"},
}


class Answer:
    def __init__(self, result):
        self.result = result
        self.asked = 0

    async def __call__(self, context, params):
        self.asked += 1
        return self.result


def call(tool, arguments, callback):
    async def run():
        async with Client(build_server(PROFILES), elicitation_callback=callback, mode="legacy") as client:
            listed = {t.name for t in (await client.list_tools()).tools}
            assert tool in listed, f"{tool} is not advertised"
            return await client.call_tool(tool, {"project_id": "proj-1", **arguments})

    return asyncio.run(run())


def test_every_confirm_gated_tool_is_covered():
    async def gated():
        async with Client(build_server(PROFILES)) as client:
            return {
                t.name for t in (await client.list_tools()).tools if "confirm" in t.input_schema["properties"]
            }

    assert asyncio.run(gated()) - {"model_install"} <= set(GATED_CALLS)


@pytest.mark.parametrize(
    "answer",
    [types.ElicitResult(action="decline"), types.ElicitResult(action="accept", content={"confirmed": False})],
    ids=["decline", "answer-no"],
)
@pytest.mark.parametrize("tool", sorted(GATED_CALLS))
def test_declined_write_changes_nothing(tool, answer, recording_platform):
    callback = Answer(answer)
    result = call(tool, GATED_CALLS[tool], callback)

    text = result.content[0].text
    assert "mutating call" not in text, text
    assert callback.asked == 1, f"{tool} never asked the user: {text}"
    assert result.is_error is True
    assert "did not confirm" in text or "Refusing to" in text, text
