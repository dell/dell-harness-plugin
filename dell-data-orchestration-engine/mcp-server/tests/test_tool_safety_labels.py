# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every advertised tool's MCP safety labels, pinned in one reviewable table.

Clients use these hints to decide what to auto-approve, what to retry and what to warn about,
so a wrong label is a safety defect, not a cosmetic one. Changing a row is a deliberate,
reviewed diff. The rules applied:

* ``destructive`` -- the call deletes something, or overwrites existing state in place
  (replacing a subset filter, a findings map, settings, node code). Purely additive writes
  (create, upload, add a node, write predictions) are not destructive.
* ``idempotent`` -- repeating the call with the same arguments has no further effect. Deleting
  or overwriting an exact target is idempotent; anything that creates, appends, runs or bills
  again on every call is not.
* ``open_world`` -- the call reads or installs third-party marketplace content. Everything else
  stays inside the caller's own DOE organisation.
"""

import asyncio

import pytest
from mcp import Client

from doe_mcp.server import PROFILES, build_server

# name: (read_only, destructive, idempotent, open_world)
LABELS: dict[str, tuple[bool, bool, bool, bool]] = {
    # discovery: read-only
    "project_list": (True, False, True, False),
    "project_get": (True, False, True, False),
    "dataset_list": (True, False, True, False),
    "dataset_get": (True, False, True, False),
    "dataset_profile": (True, False, True, False),
    "item_list": (True, False, True, False),
    "item_get": (True, False, True, False),
    "annotation_list": (True, False, True, False),
    "annotation_get": (True, False, True, False),
    "annotation_labels": (True, False, True, False),
    "pipeline_list": (True, False, True, False),
    "pipeline_get": (True, False, True, False),
    "pipeline_stats": (True, False, True, False),
    "pipeline_validate": (True, False, True, False),
    "execution_list": (True, False, True, False),
    "model_list": (True, False, True, False),
    "model_get": (True, False, True, False),
    "app_list": (True, False, True, False),
    "trigger_list": (True, False, True, False),
    "trigger_get": (True, False, True, False),
    "pii_report": (True, False, True, False),
    "pii_filter": (True, False, True, False),
    "pii_writeback_code": (True, False, True, False),
    "dataset_embeddings_status": (True, False, True, False),
    "dataset_cluster": (True, False, True, False),
    "intent_route": (True, False, True, False),
    # third-party marketplace content
    "marketplace_search": (True, False, True, True),
    "marketplace_get_dpk": (True, False, True, True),
    "model_install": (False, False, False, True),
    "pipeline_create_from_template": (False, False, False, True),
    # additive writes
    "dataset_create": (False, False, False, False),
    "item_upload": (False, False, False, False),
    "annotation_create": (False, False, False, False),
    "pipeline_create": (False, False, False, False),
    "pipeline_install": (False, False, False, False),
    "pipeline_node_add": (False, False, False, False),
    "pipeline_connect_nodes": (False, False, False, False),
    "pipeline_execute": (False, False, False, False),
    "model_create": (False, False, False, False),
    "model_deploy": (False, False, False, False),
    "model_train": (False, False, False, False),
    "model_predict": (False, False, False, False),
    "trigger_create": (False, False, False, False),
    "dataset_embed": (False, False, False, False),
    "dataset_visual_search": (False, False, False, False),
    "rag_pipeline_create": (False, False, False, False),
    "rag_add_pdf_filter_node": (False, False, False, False),
    "rag_add_pdf_to_text_node": (False, False, False, False),
    "rag_add_text_to_chunks_node": (False, False, False, False),
    "rag_add_embedding_node": (False, False, False, False),
    "rag_add_output_node": (False, False, False, False),
    "rag_query": (False, False, False, False),
    # Deterministic: the same arguments adopt the identical existing pipeline.
    "rag_pipeline_build": (False, False, True, False),
    # Pausing an already paused pipeline changes nothing.
    "pipeline_pause": (False, False, True, False),
    # overwrite existing state in place
    "dataset_update": (False, True, True, False),
    "item_update": (False, True, True, False),
    "pipeline_update_settings": (False, True, True, False),
    "pipeline_node_update": (False, True, True, False),
    "pipeline_reset": (False, True, True, False),
    "model_set_subset": (False, True, True, False),
    "pii_set_findings": (False, True, True, False),
    # delete an exact target
    "dataset_delete": (False, True, True, False),
    "annotation_delete": (False, True, True, False),
    "annotation_delete_by_model": (False, True, True, False),
    "pipeline_delete": (False, True, True, False),
    "pipeline_node_remove": (False, True, True, False),
    "pipeline_disconnect_nodes": (False, True, True, False),
    "model_delete_subset": (False, True, True, False),
    "model_delete": (False, True, True, False),
    "app_uninstall": (False, True, True, False),
    "trigger_delete": (False, True, True, False),
    "execution_terminate": (False, True, True, False),
}

ALL_PROFILES = sorted(PROFILES)


def _labels(profiles: list[str]) -> dict[str, tuple[object, ...]]:
    async def run():
        async with Client(build_server(profiles=profiles)) as client:
            tools = (await client.list_tools()).tools
        return {
            tool.name: (
                tool.annotations.read_only_hint,
                tool.annotations.destructive_hint,
                tool.annotations.idempotent_hint,
                tool.annotations.open_world_hint,
            )
            for tool in tools
        }

    return asyncio.run(run())


@pytest.mark.parametrize("profile", [None, *ALL_PROFILES], ids=lambda p: p or "full")
def test_every_advertised_tool_matches_the_label_table(profile):
    advertised = _labels([profile] if profile else [])

    assert set(advertised) <= set(LABELS), f"unlabelled tools: {sorted(set(advertised) - set(LABELS))}"
    wrong = {name: (got, LABELS[name]) for name, got in advertised.items() if got != LABELS[name]}
    assert not wrong, f"labels differ (got, expected): {wrong}"


def test_the_table_covers_exactly_the_full_surface():
    full = set()
    for profile in [None, *ALL_PROFILES]:
        full |= set(_labels([profile] if profile else []))

    assert full == set(LABELS)


def test_read_only_tools_are_never_destructive():
    assert [name for name, (ro, de, _, _) in LABELS.items() if ro and de] == []
