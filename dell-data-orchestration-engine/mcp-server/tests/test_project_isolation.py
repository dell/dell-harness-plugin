# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""R04: a resource owned by another project is rejected before anything reads or writes it.

Each case hands a tool an id whose lookup returns another project's resource, then asserts
both that the call fails and that the downstream mutation was never attempted. A mismatch
caught after the write would satisfy the first assertion and still leak.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import dtlpy as dl

from doe_mcp.tools import executions, models, pipeline_nodes, rag, triggers
from tests.test_pipeline_nodes import build_pipeline

FOREIGN = "proj-other"


class Repo:
    """A repository whose ``get`` returns a resource owned by ``owner``."""

    def __init__(self, owner, **extra):
        self.owner = owner
        self.extra = extra
        self.deleted = MagicMock()

    def get(self, **lookup):
        return SimpleNamespace(id=next(iter(lookup.values())), project_id=self.owner, **self.extra)

    def delete(self, **kwargs):
        self.deleted(**kwargs)
        return True


def _assert_rejected(result, kind):
    assert result.is_error is True
    text = result.content[0].text
    assert f"{kind} lookup failed" in text
    assert f"belongs to project '{FOREIGN}'" in text


def test_model_delete_rejects_another_projects_model_before_deleting(monkeypatch, call_tool):
    project = SimpleNamespace(id="proj-1", models=Repo(FOREIGN))
    project.models.delete = MagicMock()
    monkeypatch.setattr(models, "get_project", lambda project_id: project)

    result = call_tool("model_delete", {"project_id": "proj-1", "model_id": "m-1", "confirm": True})

    _assert_rejected(result, "model")
    project.models.delete.assert_not_called()


def test_model_set_subset_rejects_a_model_whose_dataset_is_foreign(monkeypatch, call_tool):
    model = SimpleNamespace(id="m-1", project_id="proj-1", dataset_id="ds-x", add_subset=MagicMock())
    project = SimpleNamespace(
        id="proj-1",
        models=SimpleNamespace(get=lambda **_: model),
        datasets=Repo(FOREIGN),
    )
    monkeypatch.setattr(models, "get_project", lambda project_id: project)

    result = call_tool(
        "model_set_subset",
        {"project_id": "proj-1", "model_id": "m-1", "subset": "train", "annotated_only": True},
    )

    _assert_rejected(result, "dataset")
    model.add_subset.assert_not_called()


def test_execution_terminate_rejects_another_projects_execution(monkeypatch, call_tool):
    execution = SimpleNamespace(id="e-1", project_id=FOREIGN, terminate=MagicMock())
    repository = MagicMock()
    repository.get.return_value = execution
    monkeypatch.setattr(executions, "get_project", lambda project_id: SimpleNamespace(id="proj-1"))
    monkeypatch.setattr(dl.repositories, "Executions", lambda **_: repository)

    result = call_tool(
        "execution_terminate", {"project_id": "proj-1", "execution_id": "e-1", "confirm": True}
    )

    _assert_rejected(result, "execution")
    execution.terminate.assert_not_called()


def test_trigger_delete_rejects_another_projects_trigger(monkeypatch, call_tool):
    project = SimpleNamespace(id="proj-1", triggers=Repo(FOREIGN))
    monkeypatch.setattr(triggers, "get_project", lambda project_id: project)

    result = call_tool("trigger_delete", {"project_id": "proj-1", "trigger_id": "t-1", "confirm": True})

    _assert_rejected(result, "trigger")
    project.triggers.deleted.assert_not_called()


def _pipeline_in(monkeypatch, datasets_owner="proj-1", models_owner="proj-1"):
    project = SimpleNamespace(id="proj-1", name="p", datasets=Repo(datasets_owner), models=Repo(models_owner))
    pipeline = build_pipeline(project=project)
    monkeypatch.setattr(pipeline_nodes, "get_pipeline", lambda project_id, pipeline_id: pipeline)
    return pipeline


def test_dataset_node_rejects_another_projects_dataset_before_writing(monkeypatch, call_tool):
    pipeline = _pipeline_in(monkeypatch, datasets_owner=FOREIGN)

    result = call_tool(
        "pipeline_node_add",
        {
            "project_id": "proj-1",
            "pipeline_id": "pipe-1",
            "node": {"node_type": "dataset", "name": "ingest", "dataset_id": "ds-x"},
        },
    )

    _assert_rejected(result, "dataset")
    assert list(pipeline.nodes) == []


def test_rag_code_node_rejects_a_foreign_output_dataset_before_writing(monkeypatch, call_tool):
    pipeline = _pipeline_in(monkeypatch, datasets_owner=FOREIGN)

    result = call_tool(
        "rag_add_text_to_chunks_node",
        {"project_id": "proj-1", "pipeline_id": "pipe-1", "output_dataset_id": "ds-x"},
    )

    _assert_rejected(result, "dataset")
    assert list(pipeline.nodes) == []


def test_rag_embedding_node_rejects_a_foreign_model_before_writing(monkeypatch, call_tool):
    pipeline = _pipeline_in(monkeypatch, models_owner=FOREIGN)

    result = call_tool(
        "rag_add_embedding_node",
        {"project_id": "proj-1", "pipeline_id": "pipe-1", "output_dataset_id": "ds-1", "model_id": "m-x"},
    )

    _assert_rejected(result, "model")
    assert list(pipeline.nodes) == []


def test_rag_query_rejects_a_foreign_model_before_uploading(monkeypatch, call_tool):
    dataset = SimpleNamespace(id="ds-1", items=MagicMock(), features=MagicMock(), feature_sets=MagicMock())
    project = SimpleNamespace(id="proj-1", models=Repo(FOREIGN))
    monkeypatch.setattr(rag, "get_dataset", lambda project_id, dataset_id: dataset)
    monkeypatch.setattr(rag, "get_project", lambda project_id: project)

    result = call_tool(
        "rag_query",
        {"project_id": "proj-1", "dataset_id": "ds-1", "query": "q", "model_id": "m-x"},
    )

    _assert_rejected(result, "model")
    dataset.items.upload.assert_not_called()


def test_rag_pipeline_create_rejects_a_foreign_output_dataset(monkeypatch, call_tool):
    class Datasets:
        def get(self, dataset_id):
            owner = FOREIGN if dataset_id == "ds-out" else "proj-1"
            return SimpleNamespace(id=dataset_id, project_id=owner)

    project = SimpleNamespace(id="proj-1", datasets=Datasets(), pipelines=MagicMock())
    monkeypatch.setattr(rag, "get_project", lambda project_id: project)

    result = call_tool(
        "rag_pipeline_create",
        {"project_id": "proj-1", "name": "r", "source_dataset_id": "ds-in", "output_dataset_id": "ds-out"},
    )

    _assert_rejected(result, "dataset")
    project.pipelines.create.assert_not_called()


def test_trigger_create_rejects_another_projects_pipeline(monkeypatch, call_tool):
    project = SimpleNamespace(id="proj-1", pipelines=Repo(FOREIGN), triggers=MagicMock())
    monkeypatch.setattr(triggers, "get_dataset", lambda p, d: SimpleNamespace(id=d, name="ds"))
    monkeypatch.setattr(triggers, "get_project", lambda project_id: project)

    result = call_tool(
        "trigger_create",
        {"project_id": "proj-1", "name": "t", "dataset_id": "ds-1", "pipeline_id": "p-x", "confirm": True},
    )

    _assert_rejected(result, "pipeline")
    project.triggers.create.assert_not_called()


def test_model_create_rejects_another_projects_dataset(monkeypatch, call_tool):
    project = SimpleNamespace(id="proj-1", apps=Repo("proj-1"), datasets=Repo(FOREIGN), models=MagicMock())
    monkeypatch.setattr(models, "get_project", lambda project_id: project)

    result = call_tool(
        "model_create",
        {
            "project_id": "proj-1",
            "app_id": "a-1",
            "dpk_model_name": "m",
            "model_name": "n",
            "dataset_id": "ds-x",
        },
    )

    _assert_rejected(result, "dataset")
    project.models.create.assert_not_called()


def test_app_uninstall_rejects_another_projects_app(monkeypatch, call_tool):
    project = SimpleNamespace(id="proj-1", apps=Repo(FOREIGN))
    project.apps.uninstall = MagicMock()
    monkeypatch.setattr(models, "get_project", lambda project_id: project)

    result = call_tool("app_uninstall", {"project_id": "proj-1", "app_id": "a-x", "confirm": True})

    _assert_rejected(result, "app")
    project.apps.uninstall.assert_not_called()


def test_execution_list_rejects_another_projects_pipeline(monkeypatch, call_tool):
    repository = MagicMock()
    monkeypatch.setattr(
        executions, "get_project", lambda project_id: SimpleNamespace(id="proj-1", pipelines=Repo(FOREIGN))
    )
    monkeypatch.setattr(dl.repositories, "Executions", lambda **_: repository)

    result = call_tool("execution_list", {"project_id": "proj-1", "pipeline_id": "p-x"})

    _assert_rejected(result, "pipeline")
    repository.list.assert_not_called()
