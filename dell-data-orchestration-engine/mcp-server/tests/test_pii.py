# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""PII reporting and filtering.

The marker convention is caller-supplied because Presidio's output contract could not be
verified offline; these tests pin the behaviour that follows from that choice — above all that
an absent marker is never reported as "no PII" without saying why that might be wrong.
"""

import json
from types import SimpleNamespace

import pytest

from doe_mcp.tools import models, pii
from tests.test_items import fake_item

ARGS = {"project_id": "proj-1", "dataset_id": "ds-1"}
META_MARKER = {"metadata_field": "pii_detected"}
LABEL_MARKER = {"annotation_label": "pii"}


class FakeItemsRepo:
    def __init__(self, count=3, items=None):
        self.count = count
        self.filters = None
        self._items = items if items is not None else [fake_item(), fake_item("item-2", "scan.pdf")]

    def list(self, filters=None):
        self.filters = filters
        return SimpleNamespace(items=list(self._items), items_count=self.count)


@pytest.fixture
def dataset(monkeypatch):
    repo = FakeItemsRepo()
    ds = SimpleNamespace(id="ds-1", name="docs", items=repo, items_count=50)
    monkeypatch.setattr(pii, "get_dataset", lambda project_id, dataset_id: ds)
    return ds


# --- reporting --------------------------------------------------------------------------


def test_report_answers_the_question_with_counts_and_a_sample(dataset, call_tool):
    result = call_tool("pii_report", {**ARGS, "marker": META_MARKER})

    assert result.is_error is False
    body = result.structured_content
    assert body["verdict"] == "PII markers found"
    assert body["flagged_items"] == 3
    assert body["dataset_items"] == 50
    assert len(body["sample"]) == 2


def test_report_sample_allowlists_identifiers_and_entity_types(dataset, call_tool):
    dataset.items._items = [
        fake_item(
            "item-sensitive",
            "person-ssn-1234.pdf",
            metadata={
                "user": {
                    "pii": {"US_SSN": True, "EMAIL_ADDRESS": True},
                    "raw_ssn": "111-22-3333",
                    "nested": {"email": "secret@example.com"},
                }
            },
        )
    ]

    body = call_tool(
        "pii_report", {**ARGS, "marker": {"entity_types": ["US_SSN", "CREDIT_CARD"]}}
    ).structured_content

    assert body["sample"] == [{"item_id": "item-sensitive", "matched_entity_types": ["US_SSN"]}]
    serialized = json.dumps(body)
    assert "111-22-3333" not in serialized
    assert "secret@example.com" not in serialized
    assert "person-ssn-1234.pdf" not in serialized


def test_report_does_not_claim_a_clean_dataset_when_the_marker_is_simply_absent(dataset, call_tool):
    dataset.items.count = 0
    dataset.items._items = []

    body = call_tool("pii_report", {**ARGS, "marker": META_MARKER}).structured_content

    assert body["flagged_items"] == 0
    # The dangerous reading of a zero is "this dataset is clean". Saying so without the caveat
    # would let an unrun scan or a wrong marker pass as a clean bill of health.
    assert body["caveat"] is not None
    assert "false negative" in body["caveat"]


def test_report_flags_a_count_that_is_only_one_page(dataset, call_tool):
    dataset.items.list = lambda filters=None: SimpleNamespace(items=[fake_item()], items_count=None)

    body = call_tool("pii_report", {**ARGS, "marker": META_MARKER}).structured_content

    assert body["flagged_count_is_exact"] is False
    assert "bulk action" in body["caveat"]


def test_report_echoes_the_marker_so_a_wrong_convention_is_visible(dataset, call_tool):
    body = call_tool("pii_report", {**ARGS, "marker": META_MARKER}).structured_content

    assert body["marker"] == {"metadata_field": "pii_detected", "metadata_value": True}


# --- marker validation ------------------------------------------------------------------


def test_marker_requires_exactly_one_shape(dataset, call_tool):
    both = call_tool("pii_report", {**ARGS, "marker": {**META_MARKER, **LABEL_MARKER}})
    neither = call_tool("pii_report", {**ARGS, "marker": {}})

    assert both.is_error is True
    assert neither.is_error is True


# --- filters ----------------------------------------------------------------------------


def test_exclude_filter_negates_a_metadata_marker(dataset, call_tool):
    body = call_tool("pii_filter", {**ARGS, "marker": META_MARKER, "mode": "exclude"}).structured_content

    assert body["mode"] == "exclude"
    rendered = str(body["filter"])
    assert "metadata.user.pii_detected" in rendered
    assert "$ne" in rendered or "ne" in rendered
    # Metadata markers negate cleanly, so no caveat is warranted.
    assert body["warning"] is None


def test_exclude_by_annotation_label_is_rejected(dataset, call_tool):
    result = call_tool("pii_filter", {**ARGS, "marker": LABEL_MARKER, "mode": "exclude"})
    assert result.is_error
    assert 'mixed-label' in result.content[0].text


def test_only_mode_selects_the_flagged_items(dataset, call_tool):
    body = call_tool("pii_filter", {**ARGS, "marker": META_MARKER, "mode": "only"}).structured_content

    assert body["mode"] == "only"
    assert body["warning"] is None


# --- applying the exclusion to training -------------------------------------------------


def test_set_subset_can_exclude_pii_items_from_training(project, call_tool):
    result = call_tool(
        "model_set_subset",
        {
            "project_id": "proj-1",
            "model_id": "model-1",
            "subset": "train",
            "exclude_pii": META_MARKER,
        },
    )

    assert result.is_error is False
    assert result.structured_content["pii_excluded"] is True
    rendered = str(project.models.subsets["train"].prepare())
    assert "metadata.user.pii_detected" in rendered


def test_set_subset_pii_exclusion_alone_is_enough_of_a_filter(project, call_tool):
    """Excluding PII is a real constraint, so it must satisfy the 'not unfiltered' guard."""
    result = call_tool(
        "model_set_subset",
        {
            "project_id": "proj-1",
            "model_id": "model-1",
            "subset": "validation",
            "exclude_pii": META_MARKER,
        },
    )

    assert result.is_error is False


def test_subset_membership_excludes_pii_only_and_mixed_label_items(project, call_tool):
    records = [
        {'id': 'clean', 'hidden': False, 'type': 'file', 'labels': ['ordinary'],
         'metadata.user.pii_detected': False},
        {'id': 'pii-only', 'hidden': False, 'type': 'file', 'labels': ['pii'],
         'metadata.user.pii_detected': True},
        {'id': 'mixed', 'hidden': False, 'type': 'file', 'labels': ['pii', 'ordinary'],
         'metadata.user.pii_detected': True},
    ]

    def selected(filters):
        # Execute the generated filter against a synthetic nonproduction dataset.
        clauses = filters.prepare()['filter']['$and']
        def matches(row):
            for clause in clauses:
                for field, condition in clause.items():
                    if isinstance(condition, dict):
                        assert set(condition) == {'$ne'}
                        if row.get(field) == condition['$ne']:
                            return False
                    elif row.get(field) != condition:
                        return False
            return True
        return [row for row in records if matches(row)]

    def list_items(filters):
        result = selected(filters)
        return SimpleNamespace(items=result, items_count=len(result))

    project.datasets.get = lambda **kwargs: SimpleNamespace(
        id='ds-1', project_id='proj-1', items=SimpleNamespace(list=list_items))
    result = call_tool('model_set_subset', {
        'project_id': 'proj-1', 'model_id': 'model-1', 'subset': 'train', 'exclude_pii': META_MARKER})
    assert not result.is_error
    membership = selected(project.models.subsets['train'])
    assert [row['id'] for row in membership] == ['clean']
    assert result.structured_content['matched_items'] == len(membership) == 1


def test_set_subset_rejects_label_exclusion_before_mutation(project, call_tool):
    result = call_tool(
        "model_set_subset",
        {
            "project_id": "proj-1",
            "model_id": "model-1",
            "subset": "train",
            "exclude_pii": LABEL_MARKER,
        },
    )

    assert result.is_error is True
    assert 'mixed-label' in result.content[0].text
    assert 'train' not in project.models.subsets


@pytest.fixture
def project(monkeypatch):
    from tests.test_models import FakeApps, FakeDatasetsForSubsets, FakeModels

    proj = SimpleNamespace(
        id="proj-1",
        name="proj",
        apps=FakeApps(),
        models=FakeModels(),
        datasets=FakeDatasetsForSubsets(),
    )
    monkeypatch.setattr(models, "get_project", lambda project_id: proj)
    return proj


# --- writeback to source item metadata --------------------------------------------------


class WritebackItems(FakeItemsRepo):
    def __init__(self):
        super().__init__()
        self.updated = None

    def get(self, item_id=None, **_):
        return fake_item(item_id, metadata={"system": {"mimetype": "application/pdf"}, "user": {"a": 1}})

    def update(self, item=None, **kwargs):
        self.updated = {"item": item, **kwargs}
        return item


@pytest.fixture
def writeback(monkeypatch):
    repo = WritebackItems()
    ds = SimpleNamespace(id="ds-1", name="docs", items=repo, items_count=50)
    monkeypatch.setattr(pii, "get_dataset", lambda project_id, dataset_id: ds)
    return repo


FINDINGS = {"entity_types": ["US_SSN", "EMAIL_ADDRESS"]}
WRITE_ARGS = {**ARGS, "item_id": "item-1"}


def test_findings_are_a_flat_map_of_detected_type_to_true(writeback, call_tool):
    result = call_tool("pii_set_findings", {**WRITE_ARGS, "findings": FINDINGS})

    assert result.is_error is False
    written = writeback.updated["item"].metadata["user"]["pii"]
    # Exactly the requested shape: SSN=true, nothing else.
    assert written == {"US_SSN": True, "EMAIL_ADDRESS": True}


def test_findings_carry_no_counts_scores_or_values(writeback, call_tool):
    call_tool("pii_set_findings", {**WRITE_ARGS, "findings": FINDINGS})

    written = writeback.updated["item"].metadata["user"]["pii"]
    assert all(value is True for value in written.values())
    for forbidden in ("counts", "max_score", "matched_values", "engine", "detected"):
        assert forbidden not in written


def test_undetected_types_are_absent_rather_than_false(writeback, call_tool):
    call_tool("pii_set_findings", {**WRITE_ARGS, "findings": {"entity_types": ["US_SSN"]}})

    written = writeback.updated["item"].metadata["user"]["pii"]
    assert written == {"US_SSN": True}
    assert "EMAIL_ADDRESS" not in written


def test_writeback_preserves_other_user_metadata_and_never_touches_system(writeback, call_tool):
    call_tool("pii_set_findings", {**WRITE_ARGS, "findings": FINDINGS})

    metadata = writeback.updated["item"].metadata
    assert metadata["user"]["a"] == 1
    assert metadata["system"] == {"mimetype": "application/pdf"}
    assert "system_metadata" not in writeback.updated


def test_a_clean_item_records_an_empty_map(writeback, call_tool):
    body = call_tool("pii_set_findings", {**WRITE_ARGS, "findings": {"entity_types": []}}).structured_content

    assert writeback.updated["item"].metadata["user"]["pii"] == {}
    assert body["detected_entity_types"] == []
    # The empty map is the only record that a scan ran; nothing else marks it.
    assert "pii" in writeback.updated["item"].metadata["user"]


def test_writeback_reports_the_marker_the_other_tools_should_use(writeback, call_tool):
    body = call_tool("pii_set_findings", {**WRITE_ARGS, "findings": FINDINGS}).structured_content

    assert body["written_to"] == "metadata.user.pii"
    assert body["marker_hint"] == {"entity_types": ["EMAIL_ADDRESS", "US_SSN"]}


def test_duplicate_entity_types_collapse(writeback, call_tool):
    call_tool("pii_set_findings", {**WRITE_ARGS, "findings": {"entity_types": ["US_SSN", "US_SSN"]}})

    assert writeback.updated["item"].metadata["user"]["pii"] == {"US_SSN": True}


# --- querying the flat map --------------------------------------------------------------


def test_report_matches_any_of_the_named_entity_types(dataset, call_tool):
    body = call_tool(
        "pii_report", {**ARGS, "marker": {"entity_types": ["US_SSN", "CREDIT_CARD"]}}
    ).structured_content

    assert body["flagged_items"] == 3
    rendered = str(dataset.items.filters.prepare())
    assert "metadata.user.pii.US_SSN" in rendered
    assert "metadata.user.pii.CREDIT_CARD" in rendered


def test_exclusion_by_entity_type_negates_cleanly_with_no_warning(dataset, call_tool):
    body = call_tool(
        "pii_filter", {**ARGS, "marker": {"entity_types": ["US_SSN"]}, "mode": "exclude"}
    ).structured_content

    rendered = str(body["filter"])
    assert "metadata.user.pii.US_SSN" in rendered
    # Unlike the annotation-label shape, a per-type metadata marker needs no caveat.
    assert body["warning"] is None


def test_marker_still_requires_exactly_one_shape(dataset, call_tool):
    two = call_tool("pii_report", {**ARGS, "marker": {"entity_types": ["US_SSN"], **LABEL_MARKER}})

    assert two.is_error is True


# --- the pipeline code node -------------------------------------------------------------


def test_writeback_code_is_a_single_top_level_function(call_tool):
    code = call_tool("pii_writeback_code", {}).structured_content["code"]

    assert code.startswith("def write_pii_findings(item):")
    assert len([line for line in code.splitlines() if line.startswith("def ")]) == 1


def test_writeback_code_never_writes_values_counts_or_scores(call_tool):
    body = call_tool("pii_writeback_code", {}).structured_content

    assert body["stores_matched_values"] is False
    for forbidden in ("matched_values", "counts", "max_score", "score"):
        assert forbidden not in body["code"]


def test_writeback_code_declares_the_input_field_it_assumes(call_tool):
    body = call_tool("pii_writeback_code", {}).structured_content

    assert body["reads_from"] == "metadata.user.presidio_results"
    assert "assumption" in body["caveat"]
    # The symptom of a wrong assumption must be stated, since it is silent.
    assert "empty map" in body["caveat"]


def test_writeback_code_compiles_and_produces_the_flat_map(call_tool):
    """It runs on DOE's runners, so compiling it here is the only offline check."""
    body = call_tool("pii_writeback_code", {}).structured_content
    namespace: dict = {}
    exec(compile(body["code"], "<writeback>", "exec"), namespace)  # noqa: S102

    class StubItem:
        def __init__(self, metadata):
            self.metadata = metadata
            self.updated = False

        def update(self):
            self.updated = True

    item = StubItem(
        {
            "system": {"mimetype": "text/plain"},
            "user": {
                "presidio_results": [
                    {"entity_type": "US_SSN", "score": 0.8},
                    {"entity_type": "US_SSN", "score": 0.95},
                    {"entity_type": "EMAIL_ADDRESS", "score": 0.7},
                ]
            },
        }
    )
    namespace["write_pii_findings"](item)

    # Two SSN hits collapse to one true: the map says which kinds, not how many.
    assert item.metadata["user"]["pii"] == {"US_SSN": True, "EMAIL_ADDRESS": True}
    assert item.updated is True
    assert item.metadata["system"] == {"mimetype": "text/plain"}


def test_writeback_code_writes_an_empty_map_for_a_clean_item(call_tool):
    body = call_tool("pii_writeback_code", {}).structured_content
    namespace: dict = {}
    exec(compile(body["code"], "<writeback>", "exec"), namespace)  # noqa: S102

    class StubItem:
        def __init__(self):
            self.metadata = {"user": {"presidio_results": []}}

        def update(self):
            pass

    item = StubItem()
    namespace["write_pii_findings"](item)

    assert item.metadata["user"]["pii"] == {}


def test_writeback_code_ignores_a_finding_with_no_entity_type(call_tool):
    body = call_tool("pii_writeback_code", {}).structured_content
    namespace: dict = {}
    exec(compile(body["code"], "<writeback>", "exec"), namespace)  # noqa: S102

    class StubItem:
        def __init__(self):
            self.metadata = {"user": {"presidio_results": [{"score": 0.9}, {"entity_type": "US_SSN"}]}}

        def update(self):
            pass

    item = StubItem()
    namespace["write_pii_findings"](item)

    # A malformed finding must not become a "None: true" key in the map.
    assert item.metadata["user"]["pii"] == {"US_SSN": True}


def test_report_sample_zero_returns_no_item_fields(dataset, call_tool):
    dataset.items._items = [
        fake_item(
            "item-sensitive",
            "person.pdf",
            metadata={"user": {"raw_ssn": "111-22-3333"}},
        )
    ]

    body = call_tool("pii_report", {**ARGS, "marker": META_MARKER, "sample": 0}).structured_content

    assert body["sample"] == []
    assert "111-22-3333" not in json.dumps(body)


@pytest.mark.parametrize(
    "entity_types",
    [["US_SSN"], ["US_SSN", "CREDIT_CARD"]],
)
def test_set_subset_uses_canonical_entity_type_exclusion(project, call_tool, entity_types):
    result = call_tool(
        "model_set_subset",
        {
            "project_id": "proj-1",
            "model_id": "model-1",
            "subset": "train",
            "exclude_pii": {"entity_types": entity_types},
        },
    )

    assert result.is_error is False
    prepared = project.models.subsets["train"].prepare()
    rendered = str(prepared)
    assert all(f"metadata.user.pii.{entity_type}" in rendered for entity_type in entity_types)
    assert "$ne" in rendered or "ne" in rendered
    if len(entity_types) == 2:
        assert "$and" in rendered.lower() or "and" in rendered.lower()
    assert result.structured_content["pii_exclusion_filter"] == prepared
