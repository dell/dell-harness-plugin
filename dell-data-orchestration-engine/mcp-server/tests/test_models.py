# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The marketplace install -> create -> deploy sequence."""

from types import SimpleNamespace

import pytest

from doe_mcp.tools import models
from tests.test_marketplace import fake_dpk


def fake_app(app_id="app-1"):
    return SimpleNamespace(
        id=app_id,
        name="Example",
        status="Installed",
        project_id="proj-1",
        dpk_name="example",
        dpk_version="1.2.0",
        scope="project",
    )


def fake_model(model_id="model-1", name="my-detector", subsets=None, removed=None, predicted=None):
    return SimpleNamespace(
        predict=lambda item_ids=None, dataset_id=None: (
            (
                predicted.append({"item_ids": item_ids, "dataset_id": dataset_id})
                if predicted is not None
                else None
            )
            or SimpleNamespace(id="exec-1")
        ),
        add_subset=lambda subset_name=None, subset_filter=None, **_: (
            subsets.__setitem__(subset_name, subset_filter) if subsets is not None else None
        ),
        delete_subset=lambda subset_name=None, **_: (
            removed.append(subset_name) if removed is not None else None
        ),
        id=model_id,
        name=name,
        status="pre-trained",
        version="1.0.0",
        project_id="proj-1",
        dataset_id="ds-1",
        input_type="image",
        output_type="box",
        labels=["person"],
        tags=[],
        description=None,
        module_name="model-adapter",
        url="https://example/model-1",
    )


def fake_service(service_id="svc-1"):
    return SimpleNamespace(
        id=service_id,
        name="example-service",
        active=True,
        module_name="model-adapter",
        package_id="pkg-1",
        project_id="proj-1",
        url="https://example/svc-1",
    )


class FakeApps:
    def __init__(self):
        self.installed: dict | None = None
        self.uninstalled: str | None = None
        self.list_filters = None

    def install(self, **kwargs):
        self.installed = kwargs
        return fake_app()

    def get(self, app_id=None):
        return fake_app(app_id)

    def uninstall(self, app_id=None):
        self.uninstalled = app_id
        return True

    def list(self, filters=None):
        self.list_filters = filters
        return SimpleNamespace(
            items=[fake_app()],
            items_count=1,
            has_next_page=False,
            page_offset=getattr(filters, "page", None),
            page_size=getattr(filters, "page_size", None),
        )


class FakeModels:
    def __init__(self):
        self.created: dict | None = None
        self.deployed: dict | None = None
        self.trained: dict | None = None
        self.deleted = None
        self.list_filters = None
        self.subsets: dict = {}
        self.removed_subsets: list = []
        self.predicted: list = []

    def train(self, **kwargs):
        self.trained = kwargs
        return None

    def create(self, **kwargs):
        self.created = kwargs
        return fake_model(name=kwargs["model_name"])

    def get(self, model_id=None, **_):
        return fake_model(
            model_id, subsets=self.subsets, removed=self.removed_subsets, predicted=self.predicted
        )

    def deploy(self, **kwargs):
        self.deployed = kwargs
        return fake_service()

    def delete(self, model=None):
        self.deleted = model
        return True

    def list(self, filters=None):
        self.list_filters = filters
        return SimpleNamespace(
            items=[fake_model()],
            items_count=1,
            has_next_page=False,
            page_offset=getattr(filters, "page", None),
            page_size=getattr(filters, "page_size", None),
        )


class FakeDatasetsForSubsets:
    def __init__(self):
        self.match_count = 7

    def get(self, dataset_id=None, **_):
        outer = self

        class _Items:
            def list(self, filters=None):
                return SimpleNamespace(items=[], items_count=outer.match_count)

        return SimpleNamespace(id=dataset_id, items=_Items())


@pytest.fixture
def project(monkeypatch):
    proj = SimpleNamespace(
        id="proj-1",
        name="proj",
        apps=FakeApps(),
        models=FakeModels(),
        datasets=FakeDatasetsForSubsets(),
    )
    monkeypatch.setattr(models, "get_project", lambda project_id: proj)
    monkeypatch.setattr(models, "marketplace_dpks", lambda: SimpleNamespace(get=lambda **kw: fake_dpk()))
    return proj


def test_install_needs_an_identifier(project, call_tool):
    result = call_tool("model_install", {"project_id": "proj-1"})

    assert result.is_error is True
    assert "dpk_id or dpk_name" in result.content[0].text


def test_install_reports_the_components_available_for_model_create(project, call_tool):
    result = call_tool("model_install", {"project_id": "proj-1", "dpk_name": "example"})

    assert result.is_error is False
    assert result.structured_content["id"] == "app-1"
    # Installing alone does not create a model, so point the caller at the next step.
    assert result.structured_content["available_model_components"] == ["example-detector"]


def test_install_waits_by_default(project, call_tool):
    call_tool("model_install", {"project_id": "proj-1", "dpk_name": "example"})

    assert project.apps.installed["wait"] is True


def test_install_requires_confirmation_for_custom_installation(project, call_tool):
    result = call_tool(
        "model_install",
        {"project_id": "proj-1", "dpk_name": "example", "custom_installation": {"compute": {}}},
    )

    assert result.is_error is True
    assert "confirm=true" in result.content[0].text
    assert project.apps.installed is None


def test_install_rejects_unknown_custom_installation_fields(project, call_tool):
    result = call_tool(
        "model_install",
        {
            "project_id": "proj-1",
            "dpk_name": "example",
            "custom_installation": {"environment": {"SECRET": "x"}},
            "confirm": True,
        },
    )

    assert result.is_error is True
    assert "Unsupported custom_installation fields" in result.content[0].text


def test_app_list_honors_page_inputs(project, call_tool):
    result = call_tool("app_list", {"project_id": "proj-1", "page": 1, "page_size": 1, "limit": 1})

    assert result.is_error is False
    assert project.apps.list_filters.page == 1
    assert project.apps.list_filters.page_size == 1
    assert result.structured_content["page"] == 1
    assert result.structured_content["page_size"] == 1


def test_create_binds_the_model_to_the_installed_app(project, call_tool):
    result = call_tool(
        "model_create",
        {
            "project_id": "proj-1",
            "app_id": "app-1",
            "dpk_model_name": "example-detector",
            "model_name": "my-detector",
            "dataset_id": "ds-1",
        },
    )

    assert result.is_error is False
    created = project.models.created
    # dtlpy refuses to create a model without an app, so the app entity must be passed on.
    assert created["app"].id == "app-1"
    assert created["dpk_model_name"] == "example-detector"
    assert created["project_id"] == "proj-1"


def test_create_refuses_configuration_overrides_without_confirmation(monkeypatch, project, call_tool):
    """Regression: `configuration` used to reach dtlpy verbatim with no confirmation boundary,
    unlike `custom_installation`/`service_config`, which both require confirm=true.

    Also pins that the gate runs *before* any platform call. It was originally evaluated inline in
    the `models.create(...)` argument list, which fired only after two round-trips and would have
    been dropped silently by any refactor that pre-built the kwargs.
    """
    looked_up = []
    monkeypatch.setattr(models, "get_project", lambda project_id: looked_up.append(project_id) or project)

    result = call_tool(
        "model_create",
        {
            "project_id": "proj-1",
            "app_id": "app-1",
            "dpk_model_name": "example-detector",
            "model_name": "my-detector",
            "configuration": {"threshold": 0.5},
        },
    )

    assert result.is_error is True
    assert "confirm=true" in result.content[0].text
    assert project.models.created is None
    assert looked_up == [], "refused before reaching the platform"


def test_create_passes_configuration_through_once_confirmed(project, call_tool):
    result = call_tool(
        "model_create",
        {
            "project_id": "proj-1",
            "app_id": "app-1",
            "dpk_model_name": "example-detector",
            "model_name": "my-detector",
            "configuration": {"threshold": 0.5},
            "confirm": True,
        },
    )

    assert result.is_error is False
    assert project.models.created["configuration"] == {"threshold": 0.5}


def test_model_list_honors_page_inputs(project, call_tool):
    result = call_tool("model_list", {"project_id": "proj-1", "page": 2, "page_size": 1, "limit": 1})

    assert result.is_error is False
    assert project.models.list_filters.page == 2
    assert project.models.list_filters.page_size == 1
    assert result.structured_content["page"] == 2
    assert result.structured_content["page_size"] == 1


def test_model_list_projects_down_to_requested_fields(project, call_tool):
    result = call_tool("model_list", {"project_id": "proj-1", "fields": ["name"]})

    for entity in result.structured_content["items"]:
        assert set(entity) == {"id", "name"}


def test_deploy_returns_the_service(project, call_tool):
    result = call_tool("model_deploy", {"project_id": "proj-1", "model_id": "model-1", "confirm": True})

    assert result.is_error is False
    assert result.structured_content["service"]["id"] == "svc-1"
    assert project.models.deployed == {"model_id": "model-1", "service_config": None}


# --- FR-005 / ADR-009: cost-incurring calls are gated even with no overrides ------------
#
# Before this gate, `confirm` was consumed only by validate_overrides, which returns early
# when no overrides are supplied — so a plain train or deploy spent money with confirm=False.


def test_train_refuses_without_confirmation(project, call_tool):
    result = call_tool("model_train", {"project_id": "proj-1", "model_id": "model-1"})

    assert result.is_error is True
    assert "confirm=true" in result.content[0].text
    # The point of the gate: no paid job was started.
    assert project.models.trained is None


def test_train_proceeds_with_confirmation(project, call_tool):
    result = call_tool("model_train", {"project_id": "proj-1", "model_id": "model-1", "confirm": True})

    assert result.is_error is False
    assert project.models.trained == {"model_id": "model-1", "service_config": None}


def test_deploy_refuses_without_confirmation(project, call_tool):
    result = call_tool("model_deploy", {"project_id": "proj-1", "model_id": "model-1"})

    assert result.is_error is True
    assert "confirm=true" in result.content[0].text
    assert project.models.deployed is None


def test_deploy_requires_confirmation_for_service_config(project, call_tool):
    result = call_tool(
        "model_deploy",
        {"project_id": "proj-1", "model_id": "model-1", "service_config": {"replicas": 4}},
    )

    assert result.is_error is True
    assert "confirm=true" in result.content[0].text
    assert project.models.deployed is None


def test_deploy_rejects_unknown_service_config_fields(project, call_tool):
    result = call_tool(
        "model_deploy",
        {
            "project_id": "proj-1",
            "model_id": "model-1",
            "service_config": {"env": {"SECRET": "x"}},
            "confirm": True,
        },
    )

    assert result.is_error is True
    assert "Unsupported service_config fields" in result.content[0].text


# --- FR-014: subset control, which is what makes the review gate binding ---------------


def test_set_subset_requires_at_least_one_filter(project, call_tool):
    result = call_tool("model_set_subset", {"project_id": "proj-1", "model_id": "model-1", "subset": "train"})

    assert result.is_error is True
    assert "at least one" in result.content[0].text


def test_set_subset_filters_on_the_review_marker(project, call_tool):
    result = call_tool(
        "model_set_subset",
        {
            "project_id": "proj-1",
            "model_id": "model-1",
            "subset": "train",
            "metadata_field": "reviewed",
        },
    )

    assert result.is_error is False
    assert project.models.subsets["train"] is not None
    # The filter must target user metadata, not the platform's system block.
    assert "metadata.user.reviewed" in str(project.models.subsets["train"].prepare())


def test_set_subset_warns_when_it_matches_nothing(project, call_tool):
    project.datasets.match_count = 0

    result = call_tool(
        "model_set_subset",
        {
            "project_id": "proj-1",
            "model_id": "model-1",
            "subset": "train",
            "metadata_field": "reviewed",
        },
    )

    assert result.structured_content["matched_items"] == 0
    # Better to say so now than to have the training job fail hours later.
    assert "no items" in result.structured_content["warning"]


def test_delete_subset_removes_the_definition(project, call_tool):
    result = call_tool(
        "model_delete_subset",
        {"project_id": "proj-1", "model_id": "model-1", "subset": "validation"},
    )

    assert result.is_error is False
    assert project.models.removed_subsets == ["validation"]


def test_model_delete_refuses_without_confirmation(project, call_tool):
    result = call_tool("model_delete", {"project_id": "proj-1", "model_id": "model-1"})

    assert result.is_error is True
    assert project.models.deleted is None


def test_app_uninstall_refuses_without_confirmation(project, call_tool):
    result = call_tool("app_uninstall", {"project_id": "proj-1", "app_id": "app-1"})

    assert result.is_error is True
    assert project.apps.uninstalled is None


def test_app_uninstall_warns_about_dependent_models(project, call_tool):
    result = call_tool("app_uninstall", {"project_id": "proj-1", "app_id": "app-1"})

    assert "stop working" in result.content[0].text


def test_app_uninstall_with_confirmation(project, call_tool):
    result = call_tool("app_uninstall", {"project_id": "proj-1", "app_id": "app-1", "confirm": True})

    assert result.is_error is False
    assert project.apps.uninstalled == "app-1"


# --- FR-020: running an installed model to learn what a dataset contains ----------------


PREDICT = {"project_id": "proj-1", "model_id": "model-1"}


def test_predict_refuses_without_confirmation(project, call_tool):
    result = call_tool("model_predict", {**PREDICT, "item_ids": ["i-1", "i-2"]})

    assert result.is_error is True
    assert "billable" in result.content[0].text
    # Nothing was spent and nothing was written.
    assert project.models.predicted == []


def test_predict_names_the_scope_it_is_about_to_charge_for(project, call_tool):
    result = call_tool("model_predict", {**PREDICT, "item_ids": ["i-1", "i-2"]})

    # The caller must be able to judge the cost before agreeing to it.
    assert "2 item(s)" in result.content[0].text


def test_predict_over_a_whole_dataset_says_so_in_the_refusal(project, call_tool):
    result = call_tool("model_predict", {**PREDICT, "dataset_id": "ds-1"})

    assert result.is_error is True
    assert "every item in dataset ds-1" in result.content[0].text


def test_predict_needs_exactly_one_scope(project, call_tool):
    both = call_tool("model_predict", {**PREDICT, "item_ids": ["i-1"], "dataset_id": "ds-1"})
    neither = call_tool("model_predict", {**PREDICT})

    assert both.is_error is True
    assert neither.is_error is True
    assert project.models.predicted == []


def test_predict_runs_when_confirmed(project, call_tool):
    result = call_tool("model_predict", {**PREDICT, "item_ids": ["i-1"], "confirm": True})

    assert result.is_error is False
    assert project.models.predicted == [{"item_ids": ["i-1"], "dataset_id": None}]
    body = result.structured_content
    assert body["execution_id"] == "exec-1"
    # Async: annotations appear later, so the caller must not read this as "done".
    assert "asynchronously" in body["note"]
    assert "not deployed" in body["note"]


# --- output_type: dtlpy ignores the component's declaration -----------------------------
#
# `models.create` reads moduleName, configuration, tags, description and status off the DPK
# model component but not `outputType`, defaulting to AnnotationType.CLASSIFICATION instead
# (dtlpy 1.125.6). Observed live: creating OpenAI CLIP's `openai-clip` component — which
# declares `outputType: embedding` — sent `'outputType': 'class'` on the wire. These tests pin
# the compensation so an upstream fix shows up as a failure here rather than a double override.


def app_with_component(output_type):
    component = {"name": "openai-clip", "moduleName": "clip-module"}
    if output_type is not None:
        component["outputType"] = output_type
    app = fake_app()
    app.dpk = SimpleNamespace(components=SimpleNamespace(models=[component]))
    return app


@pytest.fixture
def clip_project(project, monkeypatch):
    """The project fixture, with an app whose component declares an embedding output type."""
    monkeypatch.setattr(project.apps, "get", lambda app_id=None: app_with_component("embedding"))
    return project


CREATE = {
    "project_id": "proj-1",
    "app_id": "app-1",
    "dpk_model_name": "openai-clip",
    "model_name": "openai-clip-embed",
}


def test_create_takes_the_output_type_the_component_declares(clip_project, call_tool):
    result = call_tool("model_create", CREATE)

    assert result.is_error is False
    assert clip_project.models.created["output_type"] == "embedding"


def test_create_lets_the_caller_override_the_declared_output_type(clip_project, call_tool):
    call_tool("model_create", {**CREATE, "output_type": "class"})

    assert clip_project.models.created["output_type"] == "class"


def test_create_leaves_output_type_to_dtlpy_when_the_component_is_silent(project, call_tool, monkeypatch):
    """No declaration means no opinion: passing None would override dtlpy's own default."""
    monkeypatch.setattr(project.apps, "get", lambda app_id=None: app_with_component(None))

    call_tool("model_create", CREATE)

    assert "output_type" not in project.models.created


# --- model_list: an unauthorized query looks exactly like an empty project --------------


def ml_service(model_id="model-9"):
    service = fake_service()
    service.metadata = {"ml": {"modelId": model_id, "modelOperation": "deploy"}}
    return service


class FakeServices:
    def __init__(self, services):
        self._services = services

    def list(self, filters=None):
        return SimpleNamespace(items=self._services, items_count=len(self._services))


def empty_models_list(project, services):
    project.models.list = lambda filters=None: SimpleNamespace(
        items=[], items_count=0, has_next_page=False, page_offset=0, page_size=50
    )
    project.services = FakeServices(services)


def test_model_list_refuses_to_report_no_models_when_a_service_proves_otherwise(project, call_tool):
    """Observed live: an API key with `roles: []` gets an empty list, not a 403."""
    empty_models_list(project, [ml_service("model-9")])

    result = call_tool("model_list", {"project_id": "proj-1"})

    assert result.is_error is True
    text = result.content[0].text
    assert "model-9" in text
    assert "ML permissions" in text


def test_model_list_reports_an_empty_project_as_empty(project, call_tool):
    """Nothing to corroborate means the empty list is the truth, not a permission problem."""
    empty_models_list(project, [fake_service()])

    result = call_tool("model_list", {"project_id": "proj-1"})

    assert result.is_error is False
    assert result.structured_content["items"] == []


def test_model_list_survives_a_services_listing_that_fails(project, call_tool):
    """A failed second opinion must not turn a legitimately empty result into an error."""
    project.models.list = lambda filters=None: SimpleNamespace(
        items=[], items_count=0, has_next_page=False, page_offset=0, page_size=50
    )
    project.services = SimpleNamespace(list=lambda filters=None: (_ for _ in ()).throw(RuntimeError("no")))

    result = call_tool("model_list", {"project_id": "proj-1"})

    assert result.is_error is False
    assert result.structured_content["items"] == []
