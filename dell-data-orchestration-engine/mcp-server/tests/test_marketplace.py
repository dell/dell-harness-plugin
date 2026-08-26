"""Marketplace queries.

The marketplace is the app registry, so these assert on the DPK query we send. Whether
``systemSpace`` is exactly the right switch for the shared catalogue is still to be
confirmed against a live organization; pinning the query here makes that easy to adjust.
"""

from types import SimpleNamespace

import pytest

from doe_mcp.tools import marketplace


def fake_template_dpk(name="rag-templates"):
    """A template-only package: no model components at all.

    This is the shape that the models_only default hides, which is why templates_only has to
    relax that filter.
    """
    dpk = fake_dpk(name=name, with_models=False)
    dpk.components.pipeline_templates = [
        {
            "name": "RAG Preprocessing Template",
            "description": "chunk and embed documents",
            "nodes": [{"name": "extract", "type": "code"}, {"name": "store", "type": "storage"}],
            "connections": [],
        },
        {"name": "RAG Template", "nodes": [{"name": "retrieve", "type": "code"}], "connections": []},
    ]
    return dpk


def fake_dpk(name="example", with_models=True):
    components = SimpleNamespace(
        models=[
            {
                "name": "example-detector",
                "description": "detects things",
                "moduleName": "model-adapter",
                "status": "pre-trained",
                "labels": ["person", "car"],
                "configuration": {"threshold": 0.25},
            }
        ]
        if with_models
        else []
    )
    return SimpleNamespace(
        id="dpk-1",
        base_id="base-1",
        name=name,
        display_name="YOLOv8",
        version="1.2.0",
        description="object detection",
        scope="public",
        attributes={"Category": "Model"},
        tags=["detection"],
        creator="dataloop",
        components=components,
    )


class FakeDpks:
    def __init__(self):
        self.list_filters = None
        self.get_kwargs = None
        self.list_calls = 0

    def list(self, filters=None):
        self.list_filters = filters
        self.list_calls += 1
        return SimpleNamespace(
            items=[fake_dpk()],
            items_count=1,
            has_next_page=False,
            page_offset=getattr(filters, "page", None),
            page_size=getattr(filters, "page_size", None),
        )

    def get(self, **kwargs):
        self.get_kwargs = kwargs
        return fake_dpk()


@pytest.fixture
def dpks(monkeypatch):
    fake = FakeDpks()
    monkeypatch.setattr(marketplace, "marketplace_dpks", lambda: fake)
    return fake


def test_public_search_sets_system_space():
    query = marketplace.build_dpk_filters(category=None, public=True).prepare()

    assert query["systemSpace"] is True
    assert query["resource"] == "dpks"


def test_private_search_omits_system_space():
    assert "systemSpace" not in marketplace.build_dpk_filters(None, public=False).prepare()


def test_category_filters_attributes():
    query = marketplace.build_dpk_filters(category="Model", public=True).prepare()

    assert {"attributes.Category": "Model"} in query["filter"]["$and"]


def test_no_server_side_name_filter_is_sent():
    """The platform honours no partial-name operator, and an unmatched one returns nothing.

    Verified live: $match, $regex, $like and $contains all returned an empty list, even
    $match on a name known to exist. Sending one would silently hide the whole catalogue,
    so the query must carry no name clause at all.
    """
    query = marketplace.build_dpk_filters(category="Model", public=True).prepare()

    clauses = query["filter"]["$and"]
    assert not any("name" in clause for clause in clauses), clauses


def test_name_search_matches_substrings_locally(dpks, call_tool):
    result = call_tool("marketplace_search", {"name": "EXAMP"})

    assert result.is_error is False
    assert [item["name"] for item in result.structured_content["items"]] == ["example"]


def test_name_search_also_matches_display_name(dpks, call_tool):
    result = call_tool("marketplace_search", {"name": "yolov8"})

    assert result.is_error is False
    # fake_dpk's display_name is "YOLOv8" while its name is "example".
    assert [item["name"] for item in result.structured_content["items"]] == ["example"]


def test_name_search_excludes_non_matches(dpks, call_tool):
    result = call_tool("marketplace_search", {"name": "nothing-like-this"})

    assert result.is_error is False
    assert result.structured_content["items"] == []
    assert result.structured_content["total"] == 0


def test_unfiltered_browse_pages_server_side(dpks, call_tool):
    """With no local filtering to do, paging is pushed to the platform."""
    result = call_tool("marketplace_search", {"models_only": False, "page": 2, "page_size": 3, "limit": 1})

    assert result.is_error is False
    assert dpks.list_filters.page == 2
    assert dpks.list_filters.page_size == 3
    assert result.structured_content["page"] == 2
    assert result.structured_content["page_size"] == 3


def test_search_defaults_to_packages_that_provide_models(dpks, call_tool):
    """The default must not be a Category filter.

    Verified live: many model packages carry no `Category` attribute (the whole yolov8
    family among them), so `attributes.Category: Model` hid the most wanted models.
    """
    result = call_tool("marketplace_search", {})

    assert result.is_error is False
    assert "$and" not in dpks.list_filters.prepare().get("filter", {})
    assert result.structured_content["items"][0]["name"] == "example"
    assert result.structured_content["items"][0]["provides_models"] is True


def test_models_only_excludes_packages_without_model_components(monkeypatch, call_tool):
    class OnlyApps(FakeDpks):
        def list(self, filters=None):
            self.list_filters = filters
            return SimpleNamespace(
                items=[fake_dpk(name="an-app", with_models=False)],
                items_count=1,
                has_next_page=False,
                page_offset=0,
                page_size=100,
            )

    monkeypatch.setattr(marketplace, "marketplace_dpks", lambda: OnlyApps())
    result = call_tool("marketplace_search", {})

    assert result.is_error is False
    assert result.structured_content["items"] == []
    assert result.structured_content["scanned"] == 1
    assert result.structured_content["scan_truncated"] is False


def test_local_search_reports_scan_truncation(monkeypatch, call_tool):
    class Truncated(FakeDpks):
        def list(self, filters=None):
            self.list_calls += 1
            return SimpleNamespace(
                items=[fake_dpk(name=f"pkg-{self.list_calls}-{index}") for index in range(100)],
                items_count=2000,
                has_next_page=True,
                page_offset=getattr(filters, "page", None),
                page_size=getattr(filters, "page_size", None),
            )

    monkeypatch.setattr(marketplace, "marketplace_dpks", lambda: Truncated())
    result = call_tool("marketplace_search", {"name": "not-present"})

    assert result.is_error is False
    assert result.structured_content["items"] == []
    assert result.structured_content["scanned"] == marketplace.MAX_SCAN
    assert result.structured_content["scan_truncated"] is True
    assert result.structured_content["has_more"] is True


def test_category_filter_is_still_available(dpks, call_tool):
    result = call_tool("marketplace_search", {"models_only": False, "category": "Application"})

    assert result.is_error is False
    assert {"attributes.Category": "Application"} in dpks.list_filters.prepare()["filter"]["$and"]


def test_get_dpk_exposes_the_component_names_model_create_needs(dpks, call_tool):
    result = call_tool("marketplace_get_dpk", {"dpk_name": "example"})

    assert result.is_error is False
    models = result.structured_content["models"]
    assert models[0]["name"] == "example-detector"
    assert models[0]["module_name"] == "model-adapter"
    assert result.structured_content["latest_version"] == "1.2.0"


def test_templates_only_finds_packages_without_model_components(monkeypatch, call_tool):
    """The models_only default would hide template packages, so templates_only must relax it."""

    class Templates(FakeDpks):
        def list(self, filters=None):
            self.list_filters = filters
            return SimpleNamespace(
                items=[fake_template_dpk(), fake_dpk(name="a-model")],
                items_count=2,
                has_next_page=False,
                page_offset=0,
                page_size=100,
            )

    monkeypatch.setattr(marketplace, "marketplace_dpks", lambda: Templates())
    result = call_tool("marketplace_search", {"templates_only": True})

    assert result.is_error is False
    names = [item["name"] for item in result.structured_content["items"]]
    assert names == ["rag-templates"]
    assert result.structured_content["items"][0]["provides_pipeline_templates"] is True
    assert result.structured_content["items"][0]["provides_models"] is False


def test_get_dpk_lists_pipeline_templates(monkeypatch, call_tool):
    monkeypatch.setattr(
        marketplace, "marketplace_dpks", lambda: SimpleNamespace(get=lambda **kw: fake_template_dpk())
    )
    result = call_tool("marketplace_get_dpk", {"dpk_name": "rag-templates"})

    assert result.is_error is False
    templates = result.structured_content["pipeline_templates"]
    assert [template["name"] for template in templates] == ["RAG Preprocessing Template", "RAG Template"]
    assert templates[0]["node_count"] == 2
    assert templates[0]["node_types"] == ["code", "storage"]


def test_get_dpk_needs_an_identifier(dpks, call_tool):
    result = call_tool("marketplace_get_dpk", {})

    assert result.is_error is True
    assert "dpk_id or dpk_name" in result.content[0].text
