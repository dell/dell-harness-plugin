"""Creating a pipeline from a marketplace DPK's pipeline template.

dtlpy has no "install pipeline template" call: templates are raw pipeline JSON on
``components.pipeline_templates``, instantiated through ``pipelines.create(pipeline_json=...)``.
These tests pin that flow, plus the validate-then-confirm gate around it.
"""

from types import SimpleNamespace

import pytest

from doe_mcp.tools import pipelines
from tests.test_marketplace import fake_dpk, fake_template_dpk


def fake_pipeline(pipeline_id="pipe-1", name="RAG Template"):
    return SimpleNamespace(
        id=pipeline_id,
        name=name,
        status="Created",
        project_id="proj-1",
        description=None,
        created_at="2026-01-01",
        updated_at="2026-01-01",
        nodes=[],
        connections=[],
        start_nodes=[],
        url="https://example/pipe-1",
    )


_VALID = {"valid": True}


class FakePipelines:
    def __init__(self, validation=_VALID):
        self.created: dict | None = None
        self.validated: dict | None = None
        self._validation = validation

    def validate(self, pipeline_json=None):
        self.validated = pipeline_json
        return self._validation

    def create(self, name=None, project_id=None, pipeline_json=None):
        self.created = {"name": name, "project_id": project_id, "pipeline_json": pipeline_json}
        return fake_pipeline(name=name)


@pytest.fixture
def project(monkeypatch):
    repo = FakePipelines()
    proj = SimpleNamespace(id="proj-1", name="proj", pipelines=repo)
    monkeypatch.setattr(pipelines, "get_project", lambda project_id: proj)
    monkeypatch.setattr(
        pipelines, "marketplace_dpks", lambda: SimpleNamespace(get=lambda **kw: fake_template_dpk())
    )
    return proj


# --- template selection ----------------------------------------------------------------


def test_named_template_is_selected():
    templates = [{"name": "a"}, {"name": "b"}]

    assert pipelines.select_template(templates, "b") == {"name": "b"}


def test_single_template_needs_no_name():
    assert pipelines.select_template([{"name": "only"}], None) == {"name": "only"}


def test_ambiguous_selection_lists_the_choices():
    with pytest.raises(ValueError, match="several pipeline templates: a, b"):
        pipelines.select_template([{"name": "a"}, {"name": "b"}], None)


def test_unknown_template_name_lists_the_choices():
    with pytest.raises(ValueError, match="Available templates: a, b"):
        pipelines.select_template([{"name": "a"}, {"name": "b"}], "c")


def test_package_without_templates_is_rejected():
    with pytest.raises(ValueError, match="no pipeline templates"):
        pipelines.select_template([], None)


def test_failed_validation_payload_is_rejected():
    assert pipelines.validation_passed({"valid": False, "errors": ["bad node"]}) is False


def test_validation_payload_with_errors_is_rejected():
    assert pipelines.validation_passed({"valid": True, "errors": ["bad node"]}) is False


def test_template_pipeline_json_adds_missing_positions_without_mutating_template():
    template = {"name": "tmpl", "nodes": [{"name": "a"}, {"name": "b", "metadata": {}}]}

    result = pipelines.template_pipeline_json(template, project_id="proj-1", pipeline_name="pipe")

    assert result["name"] == "pipe"
    assert result["projectId"] == "proj-1"
    assert result["nodes"][0]["metadata"]["position"] == {"x": 220, "y": 180}
    assert result["nodes"][1]["metadata"]["position"] == {"x": 580, "y": 180}
    assert "metadata" not in template["nodes"][0]


# --- the tool --------------------------------------------------------------------------


def test_create_from_template_refuses_without_confirmation(project, call_tool):
    result = call_tool(
        "pipeline_create_from_template",
        {"project_id": "proj-1", "dpk_name": "rag-templates", "template_name": "RAG Template"},
    )

    assert result.is_error is True
    assert "confirm=true" in result.content[0].text
    assert project.pipelines.created is None, "nothing may be created without confirmation"


def test_create_from_template_validates_before_asking_for_confirmation(project, call_tool):
    """A broken template must fail cheaply, before the user is asked to confirm."""
    call_tool(
        "pipeline_create_from_template",
        {"project_id": "proj-1", "dpk_name": "rag-templates", "template_name": "RAG Template"},
    )

    assert project.pipelines.validated is not None
    assert project.pipelines.validated["name"] == "RAG Template"


def test_create_from_template_sends_the_template_graph(project, call_tool):
    result = call_tool(
        "pipeline_create_from_template",
        {
            "project_id": "proj-1",
            "dpk_name": "rag-templates",
            "template_name": "RAG Preprocessing Template",
            "confirm": True,
        },
    )

    assert result.is_error is False
    sent = project.pipelines.created["pipeline_json"]
    # The template's own graph must survive; only name and project are overridden.
    assert [node["name"] for node in sent["nodes"]] == ["extract", "store"]
    assert sent["projectId"] == "proj-1"
    assert sent["name"] == "RAG Preprocessing Template"
    assert [node["metadata"]["position"] for node in sent["nodes"]] == [
        {"x": 220, "y": 180},
        {"x": 580, "y": 180},
    ]
    assert result.structured_content["template"]["node_count"] == 2


def test_create_from_template_honours_a_name_override(project, call_tool):
    result = call_tool(
        "pipeline_create_from_template",
        {
            "project_id": "proj-1",
            "dpk_name": "rag-templates",
            "template_name": "RAG Template",
            "name": "my-rag",
            "confirm": True,
        },
    )

    assert result.is_error is False
    assert project.pipelines.created["name"] == "my-rag"
    assert result.structured_content["name"] == "my-rag"


@pytest.mark.parametrize("validation", [None, {"valid": False, "errors": ["bad node"]}])
def test_create_from_template_reports_an_invalid_template(monkeypatch, call_tool, validation):
    repo = FakePipelines(validation=validation)
    proj = SimpleNamespace(id="proj-1", name="proj", pipelines=repo)
    monkeypatch.setattr(pipelines, "get_project", lambda project_id: proj)
    monkeypatch.setattr(
        pipelines, "marketplace_dpks", lambda: SimpleNamespace(get=lambda **kw: fake_template_dpk())
    )

    result = call_tool(
        "pipeline_create_from_template",
        {
            "project_id": "proj-1",
            "dpk_name": "rag-templates",
            "template_name": "RAG Template",
            "confirm": True,
        },
    )

    assert result.is_error is True
    assert "invalid" in result.content[0].text
    assert repo.created is None


def test_create_from_template_needs_a_package_identifier(project, call_tool):
    result = call_tool("pipeline_create_from_template", {"project_id": "proj-1", "confirm": True})

    assert result.is_error is True
    assert "dpk_id or dpk_name" in result.content[0].text


def test_create_from_template_rejects_a_package_without_templates(monkeypatch, project, call_tool):
    monkeypatch.setattr(pipelines, "marketplace_dpks", lambda: SimpleNamespace(get=lambda **kw: fake_dpk()))

    result = call_tool(
        "pipeline_create_from_template",
        {"project_id": "proj-1", "dpk_name": "example", "confirm": True},
    )

    assert result.is_error is True
    assert "no pipeline templates" in result.content[0].text
