# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The workflow-cost instrumentation itself must keep working (Phase 0 exit criterion).

`scripts/measure_workflow_cost.py` drives real tool calls through the in-memory MCP client for
each of the three `docs/guides/*.md` workflows. This doesn't assert a token/turn ceiling — Phase 0
only requires that a baseline gets *measured*; a budget gate on workflow cost is a later-phase
decision per the plan's Success Metrics table ("Tokens / turns per workflow: unmeasured -> set
after Phase 0"). What this pins down is that the instrumentation runs cleanly end to end and
reports a sane, nonzero shape, so it doesn't silently rot as the tools it drives change.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from measure_workflow_cost import WORKFLOWS, WorkflowResult  # noqa: E402


@pytest.mark.parametrize("name", sorted(WORKFLOWS))
def test_workflow_runs_and_reports_nonzero_cost(name):
    result = WORKFLOWS[name]()

    assert isinstance(result, WorkflowResult)
    assert result.name == name
    assert result.turns > 0
    assert result.request_chars > 0
    assert result.response_chars > 0
    assert result.total_tokens_approx > 0
