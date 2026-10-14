# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Node specs reject unusable input at construction time, before any platform call.

Complements test_dql.py (the DQL structural rules themselves) and test_code_node.py (the
code-syntax rules themselves): this file only checks that schemas.py wires those checks in.
"""

import pytest
from pydantic import ValidationError

from doe_mcp.schemas import CodeNodeSpec, DatasetNodeSpec, TaskNodeSpec


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


def test_task_node_spec_accepts_shares_that_sum_to_100():
    spec = TaskNodeSpec(
        node_type="task",
        name="label",
        dataset_id="ds-1",
        task_owner="me@example.com",
        workload=[
            {"assignee_id": "a@example.com", "load": 60},
            {"assignee_id": "b@example.com", "load": 40},
        ],
    )

    assert [unit.load for unit in spec.workload] == [60, 40]


def test_task_node_spec_rejects_shares_that_sum_over_100():
    """Regression: each unit's `load` was bounded 0-100, but nothing checked the sum, so a
    workload the platform would only reject later (e.g. three assignees each at 100%) passed
    local validation.
    """
    with pytest.raises(ValidationError, match="sum to 250"):
        TaskNodeSpec(
            node_type="task",
            name="label",
            dataset_id="ds-1",
            task_owner="me@example.com",
            workload=[
                {"assignee_id": "a@example.com", "load": 100},
                {"assignee_id": "b@example.com", "load": 100},
                {"assignee_id": "c@example.com", "load": 50},
            ],
        )


def test_task_node_spec_rejects_a_duplicate_assignee():
    with pytest.raises(ValidationError, match="duplicate assignee"):
        TaskNodeSpec(
            node_type="task",
            name="label",
            dataset_id="ds-1",
            task_owner="me@example.com",
            workload=[
                {"assignee_id": "a@example.com", "load": 50},
                {"assignee_id": "a@example.com", "load": 30},
            ],
        )
