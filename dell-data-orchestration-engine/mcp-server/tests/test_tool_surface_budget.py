"""Ratchet test for the advertised tool surface (docs/plans/AGENT_OPTIMIZATION_HARNESS_SLM_PLAN.md).

`scripts/measure_tool_surface.py` is the source of truth for how the surface is measured; this
test just asserts it doesn't regress. The ceiling starts at the measured baseline from Phase 0 and
is lowered as later phases (schema slimming) land — it must never be raised to make a regression
pass.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from measure_tool_surface import measure  # noqa: E402

# The plugin launcher now defaults to the focused plugin-v1 profile. This ceiling
# includes its typed output schemas and is lower than the former 20,650-token full-surface
# ceiling. The opt-in full server is still measured below for individual input-schema ratchets.
TOTAL_TOKEN_CEILING = 18_100
DEGENERATE_OUTPUT_SCHEMA_CEILING = 0


def test_advertised_surface_does_not_regress():
    report = measure(["plugin-v1"])
    assert report["total_tokens_approx"] <= TOTAL_TOKEN_CEILING, (
        f"Advertised tool surface grew to ~{report['total_tokens_approx']} tok, "
        f"over the {TOTAL_TOKEN_CEILING} tok ceiling. Trim it back down, or lower the ceiling here "
        "only after deliberately accepting the growth."
    )


def test_degenerate_output_schemas_do_not_regress():
    report = measure(["plugin-v1"])
    assert report["degenerate_output_schema_count"] <= DEGENERATE_OUTPUT_SCHEMA_CEILING, (
        "A new tool advertises a degenerate `dict[str, Any]` output schema. Use "
        "`structured_output=False` (see Phase 1 of the optimization plan) instead of a bare dict "
        "return annotation."
    )


# Phase 1 ceilings for the five largest input schemas (plan §1's "worst single offenders" plus
# `pii_filter`), each measured immediately after trimming `Field(description=...)` prose (see
# schemas.py's PiiMarkerSpec, tools/models.py's model_set_subset, tools/marketplace.py's
# marketplace_search) down to the shortest wording that keeps every verified-behavior caveat.
# `pipeline_node_add` barely moves: its remaining ~5,600 chars is mostly JSON Schema structure
# (the four NodeSpec variants' $defs/oneOf/discriminator scaffolding), not prose, so cutting it
# further would mean splitting it into per-node-type tools — rejected in favor of keeping the one
# tool the docs/guides already document (see plan §4 Phase 1 caution).
INPUT_SCHEMA_CEILINGS = {
    "pipeline_node_add": 5_700,
    "model_set_subset": 2_300,
    "pii_report": 1_600,
    "pii_filter": 1_600,
    "marketplace_search": 1_350,
}


def test_largest_input_schemas_are_trimmed():
    report = measure()
    input_chars = report["by_tool_input_chars"]
    for tool_name, ceiling in INPUT_SCHEMA_CEILINGS.items():
        actual = input_chars[tool_name]
        assert actual <= ceiling, (
            f"{tool_name} input schema grew to {actual} chars, over the {ceiling} char ceiling."
        )
