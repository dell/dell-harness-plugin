"""Node specs reject unusable input at construction time, before any platform call.

Complements test_dql.py (the DQL structural rules themselves) and test_code_node.py (the
code-syntax rules themselves): this file only checks that schemas.py wires those checks in.
"""

import pytest
from pydantic import ValidationError

from doe_mcp.schemas import CodeNodeSpec, DatasetNodeSpec


def test_dataset_node_spec_accepts_a_well_formed_filter():
    spec = DatasetNodeSpec(
        node_type="dataset",
        name="ingest",
        dataset_id="ds-1",
        data_filters={"filter": {"$and": [{"metadata.system.mimetype": {"$eq": "application/pdf"}}]}},
    )

    assert spec.data_filters is not None


def test_dataset_node_spec_rejects_a_malformed_dql_filter():
    with pytest.raises(ValidationError, match="needs a list of values"):
        DatasetNodeSpec(
            node_type="dataset",
            name="ingest",
            dataset_id="ds-1",
            data_filters={"filter": {"metadata.system.mimetype": {"$in": "application/pdf"}}},
        )


def test_dataset_node_spec_passes_through_an_operator_this_repo_does_not_know():
    """The platform's grammar is the contract, not our operator list — see doe_mcp.dql.

    Regression: this validator used to reject anything outside dtlpy's `FiltersOperations`, which
    blocked `$ieq` (documented by DOE) and any operator added platform-side since.
    """
    spec = DatasetNodeSpec(
        node_type="dataset",
        name="ingest",
        dataset_id="ds-1",
        data_filters={"filter": {"name": {"$ieq": "Report.PDF"}}},
    )

    assert spec.data_filters is not None


def test_dataset_node_spec_with_no_filter_is_unaffected():
    spec = DatasetNodeSpec(node_type="dataset", name="ingest", dataset_id="ds-1")

    assert spec.data_filters is None


def test_code_node_spec_accepts_valid_source():
    spec = CodeNodeSpec(node_type="code", name="filter", code="def run(item):\n    return item\n")

    assert spec.code.strip().startswith("def run")


def test_code_node_spec_rejects_a_syntax_error():
    with pytest.raises(ValidationError, match="not valid Python"):
        CodeNodeSpec(node_type="code", name="filter", code="def run(item:\n    return item\n")


def test_code_node_spec_rejects_more_than_one_top_level_statement():
    with pytest.raises(ValidationError, match="exactly one top-level function"):
        CodeNodeSpec(
            node_type="code",
            name="filter",
            code="def a():\n    pass\n\n\ndef b():\n    pass\n",
        )
