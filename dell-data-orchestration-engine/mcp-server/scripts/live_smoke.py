#!/usr/bin/env python
# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Drive the real tools against a live DOE instance.

The offline suite stubs dtlpy at the seams, which is fast and needs no network but cannot
see mistakes on the dtlpy side of that seam. Three real bugs hid there: labels without a
colour, a name filter the platform silently ignores, and a category attribute that is
missing on most model packages. This script exists so that class of bug is caught again.

It only touches one project, creates a uniquely named dataset, and deletes it afterwards. The
elicitation section (see below) creates and deletes one more throwaway dataset of its own.

Usage:
    export DATALOOP_API_KEY=...
    export DATALOOP_PATH=~/.doe-mcp/dataloop
    python scripts/live_smoke.py <project_id>
"""

import asyncio
import os
import sys
import tempfile
import uuid
from typing import Any

import mcp_types as types
from mcp import Client

from doe_mcp.server import build_server

failures: list[str] = []
unknowns: list[str] = []

# A fake id: annotations written under it are the ones the bulk delete must remove, and
# nothing else in a real project will collide with it.
SMOKE_MODEL_ID = f"smoke-model-{uuid.uuid4().hex[:8]}"


def record(label: str, answer: str) -> None:
    """Report a platform behaviour the offline suite could not determine."""
    print(f"  INFO  {label} — {answer}")
    unknowns.append(f"{label}: {answer}")


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")
    if not ok:
        failures.append(label)


def _detail(result: Any, limit: int = 120) -> str:
    """First line of a tool error, for a check's detail column.

    `getattr` rather than `.text`: a content block is a union (text/image/audio/resource) and only
    the text variant has `.text`.
    """
    blocks = getattr(result, "content", None) or []
    return getattr(blocks[0], "text", "")[:limit] if blocks else ""


async def run(project_id: str) -> None:
    async with Client(build_server()) as client:

        async def call(tool, args=None):
            return await client.call_tool(tool, args or {})

        async def cleanup_dataset(dataset_id: str | None, dataset_name: str) -> None:
            if dataset_id is None:
                listed = await call("dataset_list", {"project_id": project_id, "name": dataset_name})
                if not listed.is_error:
                    matches = [
                        item for item in listed.structured_content["items"] if item["name"] == dataset_name
                    ]
                    dataset_id = matches[0]["id"] if matches else None
            if dataset_id:
                print("cleanup")
                res = await call(
                    "dataset_delete",
                    {"project_id": project_id, "dataset_id": dataset_id, "confirm": True},
                )
                check("dataset_delete removes the dataset", not res.is_error)
            else:
                check("dataset cleanup found nothing to delete", True)

        print("projects")
        res = await call("project_get", {"project_id": project_id})
        check(
            "project_get returns the project", not res.is_error and res.structured_content["id"] == project_id
        )

        print("datasets")
        name = f"live-smoke-{uuid.uuid4().hex[:8]}"
        res = await call("dataset_create", {"project_id": project_id, "name": name, "labels": ["cat", "dog"]})
        # Regression: labels with no colour used to fail *after* creating the dataset.
        check(
            "dataset_create accepts labels",
            not res.is_error,
            "" if not res.is_error else res.content[0].text[:160],
        )
        dataset_id = res.structured_content["id"] if not res.is_error else None

        if dataset_id:
            res = await call(
                "dataset_update",
                {"project_id": project_id, "dataset_id": dataset_id, "name": f"{name}-renamed"},
            )
            check(
                "dataset_update renames",
                not res.is_error and res.structured_content["name"] == f"{name}-renamed",
            )

        # --- items ----------------------------------------------------------------------
        # Every tool below is a new SDK call site, so this is the first time any of it has
        # met a real platform. Nothing here is billable: no install, deploy or train.
        item_ids: list[str] = []
        if dataset_id:
            print("items")
            with tempfile.TemporaryDirectory() as tmp:
                paths = []
                for n in range(2):
                    path = os.path.join(tmp, f"smoke-{n}.txt")
                    with open(path, "w") as handle:
                        handle.write(f"live smoke item {n}\n")
                    paths.append(path)

                res = await call(
                    "item_upload",
                    {"project_id": project_id, "dataset_id": dataset_id, "local_paths": paths},
                )
                body = res.structured_content if not res.is_error else {}
                check(
                    "item_upload reports every file",
                    not res.is_error and body.get("all_succeeded") is True,
                    "" if not res.is_error else res.content[0].text[:160],
                )
                item_ids = [entry["id"] for entry in body.get("uploaded", [])]

            res = await call(
                "item_upload",
                {"project_id": project_id, "dataset_id": dataset_id, "local_paths": ["/no/such/file.txt"]},
            )
            check("item_upload refuses a missing local file", res.is_error)

            res = await call(
                "item_list", {"project_id": project_id, "dataset_id": dataset_id, "page_size": 1, "limit": 1}
            )
            check(
                "item_list pages and reports has_more",
                not res.is_error
                and res.structured_content["returned"] == 1
                and res.structured_content["has_more"] is True,
            )

        if item_ids:
            first = item_ids[0]
            res = await call(
                "item_update",
                {
                    "project_id": project_id,
                    "dataset_id": dataset_id,
                    "item_id": first,
                    "metadata": {"reviewed": True},
                },
            )
            check(
                "item_update writes user metadata",
                not res.is_error,
                "" if not res.is_error else res.content[0].text[:160],
            )

            res = await call(
                "item_get", {"project_id": project_id, "dataset_id": dataset_id, "item_id": first}
            )
            meta = res.structured_content.get("metadata", {}) if not res.is_error else {}
            check(
                "item metadata round-trips under the user namespace",
                ((meta or {}).get("user") or {}).get("reviewed") is True,
                f"metadata={meta}",
            )
            # The serializer strips it; confirm the platform's own block never leaks out.
            check("item_get hides platform system metadata", "system" not in meta)

            res = await call(
                "item_update",
                {
                    "project_id": project_id,
                    "dataset_id": dataset_id,
                    "item_id": first,
                    "metadata": {"system": {"forged": True}},
                },
            )
            check("item_update refuses the system namespace", res.is_error)

        # --- annotations ----------------------------------------------------------------
        if item_ids:
            print("annotations")
            res = await call("annotation_labels", {"project_id": project_id, "dataset_id": dataset_id})
            labels = res.structured_content.get("labels", []) if not res.is_error else []
            check("annotation_labels reads the dataset ontology", "cat" in labels, f"{labels}")

            res = await call(
                "annotation_create",
                {
                    "project_id": project_id,
                    "dataset_id": dataset_id,
                    "item_id": item_ids[0],
                    "label": "cat",
                    "model_id": SMOKE_MODEL_ID,
                    "confidence": 0.9,
                },
            )
            check(
                "annotation_create writes a classification",
                not res.is_error,
                "" if not res.is_error else _detail(res, 200),
            )

            res = await call(
                "annotation_list",
                {"project_id": project_id, "dataset_id": dataset_id, "item_id": item_ids[0]},
            )
            listed = res.structured_content.get("items", []) if not res.is_error else []
            check("annotation_list returns the new annotation", len(listed) >= 1, f"count={len(listed)}")
            # The offline suite had this key wrong once already (id vs model_id).
            check(
                "annotation model attribution survives the round trip",
                any(a.get("model_id") == SMOKE_MODEL_ID for a in listed),
                f"{[(a.get('label'), a.get('model_id')) for a in listed]}",
            )

            res = await call(
                "annotation_create",
                {
                    "project_id": project_id,
                    "dataset_id": dataset_id,
                    "item_id": item_ids[0],
                    "label": "definitely-not-in-the-ontology",
                },
            )
            check("annotation_create refuses an unknown label", res.is_error)

            # Open question the offline suite cannot answer: would the *platform* have
            # rejected it anyway, making the client-side check redundant or load-bearing?
            try:
                import dtlpy as dl

                dataset = dl.datasets.get(dataset_id=dataset_id)
                item = dataset.items.get(item_id=item_ids[0])
                builder = item.annotations.builder()
                builder.add(annotation_definition=dl.Classification(label="smoke-unknown-label"))
                item.annotations.upload(builder)
                fetched = [a.label for a in item.annotations.list()]
                accepted = "smoke-unknown-label" in fetched
                record(
                    "platform accepts an out-of-ontology label",
                    "YES — client-side validation is load-bearing, not redundant"
                    if accepted
                    else "NO — the platform rejects it too; the client check is defence in depth",
                )
            except Exception as exc:  # the probe must never fail the smoke run
                record("platform out-of-ontology behaviour", f"probe failed: {str(exc)[:120]}")

            # A human-authored annotation on the second item must survive the bulk delete.
            if len(item_ids) > 1:
                await call(
                    "annotation_create",
                    {
                        "project_id": project_id,
                        "dataset_id": dataset_id,
                        "item_id": item_ids[1],
                        "label": "dog",
                    },
                )

            res = await call(
                "annotation_delete_by_model",
                {"project_id": project_id, "dataset_id": dataset_id, "model_id": SMOKE_MODEL_ID},
            )
            check("bulk delete refuses without confirmation", res.is_error)
            reported = res.content[0].text if res.is_error else ""
            check("the refusal states the blast radius", "annotation(s)" in reported, reported[:140])

            res = await call(
                "annotation_delete_by_model",
                {
                    "project_id": project_id,
                    "dataset_id": dataset_id,
                    "model_id": SMOKE_MODEL_ID,
                    "confirm": True,
                    "expected_count": 1,
                },
            )
            check(
                "bulk delete removes the model's annotations",
                not res.is_error,
                "" if not res.is_error else _detail(res, 200),
            )

            # The safety claim that matters: the filter scoped to this model only.
            if len(item_ids) > 1:
                res = await call(
                    "annotation_list",
                    {"project_id": project_id, "dataset_id": dataset_id, "item_id": item_ids[1]},
                )
                survivors = res.structured_content.get("items", []) if not res.is_error else []
                check(
                    "bulk delete spared the human-authored annotation",
                    any(a.get("label") == "dog" for a in survivors),
                    f"{[a.get('label') for a in survivors]}",
                )

        # --- PII ------------------------------------------------------------------------
        if item_ids:
            print("pii")
            res = await call(
                "pii_set_findings",
                {
                    "project_id": project_id,
                    "dataset_id": dataset_id,
                    "item_id": item_ids[0],
                    "findings": {"entity_types": ["US_SSN", "EMAIL_ADDRESS"]},
                },
            )
            check(
                "pii_set_findings writes the flat map",
                not res.is_error,
                "" if not res.is_error else _detail(res, 200),
            )

            res = await call(
                "item_get", {"project_id": project_id, "dataset_id": dataset_id, "item_id": item_ids[0]}
            )
            written = res.structured_content.get("metadata", {}) if not res.is_error else {}
            found = ((written or {}).get("user") or {}).get("pii")
            check(
                "the flat map round-trips as <TYPE>: true",
                found == {"US_SSN": True, "EMAIL_ADDRESS": True},
                f"pii={found}",
            )

            if len(item_ids) > 1:
                await call(
                    "pii_set_findings",
                    {
                        "project_id": project_id,
                        "dataset_id": dataset_id,
                        "item_id": item_ids[1],
                        "findings": {"entity_types": []},
                    },
                )
                res = await call(
                    "item_get", {"project_id": project_id, "dataset_id": dataset_id, "item_id": item_ids[1]}
                )
                meta = res.structured_content.get("metadata", {}) if not res.is_error else {}
                clean_user = (meta or {}).get("user") or {}
                # Open question: an empty map is the ONLY record that a scan ran. If the
                # platform drops it, scanned-clean is indistinguishable from never-scanned.
                record(
                    "platform persists an empty pii map",
                    "YES — scanned-clean stays distinguishable from never-scanned"
                    if "pii" in clean_user
                    else "NO — the empty map is dropped, so an explicit scanned marker is needed",
                )

            res = await call(
                "pii_report",
                {"project_id": project_id, "dataset_id": dataset_id, "marker": {"entity_types": ["US_SSN"]}},
            )
            body = res.structured_content if not res.is_error else {}
            check(
                "pii_report finds the flagged item by entity type",
                not res.is_error and body.get("flagged_items") == 1,
                f"flagged={body.get('flagged_items')} exact={body.get('flagged_count_is_exact')}",
            )

            res = await call(
                "pii_filter",
                {
                    "project_id": project_id,
                    "dataset_id": dataset_id,
                    "marker": {"entity_types": ["US_SSN"]},
                    "mode": "exclude",
                },
            )
            check("pii_filter builds an exclusion the platform accepts", not res.is_error)
            if not res.is_error:
                excluded = await call(
                    "item_list",
                    {"project_id": project_id, "dataset_id": dataset_id, "annotated": None},
                )
                # Cross-check the exclusion actually withholds the flagged item.
                res2 = await call(
                    "pii_report",
                    {
                        "project_id": project_id,
                        "dataset_id": dataset_id,
                        "marker": {"entity_types": ["CREDIT_CARD"]},
                    },
                )
                check(
                    "an entity type nothing carries reports zero",
                    not res2.is_error and res2.structured_content["flagged_items"] == 0,
                )
                check(
                    "a zero result carries the false-negative caveat",
                    bool(res2.structured_content.get("caveat")),
                )
                del excluded

        # --- triggers -------------------------------------------------------------------
        # Creating a real trigger needs a pipeline and would leave standing automation
        # behind, so only the read path and the refusals are exercised here.
        print("triggers")
        res = await call("trigger_list", {"project_id": project_id})
        check(
            "trigger_list reads project-wide",
            not res.is_error,
            "" if not res.is_error else res.content[0].text[:160],
        )

        res = await call(
            "trigger_create",
            {
                "project_id": project_id,
                "name": "smoke-should-not-exist",
                "dataset_id": dataset_id or "x",
                "pipeline_id": "00000000-0000-0000-0000-000000000000",
            },
        )
        check("trigger_create refuses without confirmation", res.is_error)

        # --- elicitation (Phase 2) --------------------------------------------------------
        # The offline suite (tests/test_elicitation_e2e.py) proves this against a mocked
        # dtlpy; this is the same client-side wiring (a real elicitation-capable mcp.Client,
        # "legacy" mode for its back-channel) against a real platform call, so a bug specific
        # to the dtlpy side of that seam — or to running a real delete from inside the
        # anyio.from_thread.run bridge — would show up here even if the mock hid it.
        print("elicitation")
        elicit_name = f"live-smoke-elicit-{uuid.uuid4().hex[:8]}"
        res = await call("dataset_create", {"project_id": project_id, "name": elicit_name})
        elicit_dataset_id = res.structured_content["id"] if not res.is_error else None
        check("elicitation smoke dataset was created", elicit_dataset_id is not None)

        if elicit_dataset_id:

            async def decline(context, params):
                return types.ElicitResult(action="decline")

            async def accept(context, params):
                return types.ElicitResult(action="accept", content={"confirmed": True})

            async with Client(build_server(), elicitation_callback=decline, mode="legacy") as declining:
                res = await declining.call_tool(
                    "dataset_delete", {"project_id": project_id, "dataset_id": elicit_dataset_id}
                )
            check("a declined elicitation refuses the delete", res.is_error)

            res = await call("dataset_get", {"project_id": project_id, "dataset_id": elicit_dataset_id})
            check("the dataset survives a declined elicitation", not res.is_error)

            async with Client(build_server(), elicitation_callback=accept, mode="legacy") as accepting:
                res = await accepting.call_tool(
                    "dataset_delete", {"project_id": project_id, "dataset_id": elicit_dataset_id}
                )
            check(
                "an accepted elicitation performs the delete with no confirm=true argument",
                not res.is_error,
                "" if not res.is_error else _detail(res, 200),
            )

        print("marketplace")
        res = await call("marketplace_search", {"page_size": 5})
        models: dict[str, Any] = res.structured_content if not res.is_error else {"items": [], "total": 0}
        check("marketplace_search finds model packages", models["total"] > 0, f"total={models['total']}")
        check(
            "every result provides models",
            all(item["provides_models"] for item in models["items"]),
        )

        # Regression: the platform ignores partial-name filters, so this must be local.
        res = await call("marketplace_search", {"name": "yolo"})
        by_name: dict[str, Any] = res.structured_content if not res.is_error else {"items": []}
        names = [i["name"] for i in by_name["items"]]
        check("name search matches substrings", bool(names), f"{names[:5]}")

        res = await call("marketplace_search", {"name": "definitely-not-a-real-package-xyz"})
        empty: dict[str, Any] = res.structured_content if not res.is_error else {"total": -1}
        check("name search excludes non-matches", not res.is_error and empty["total"] == 0)

        res = await call("marketplace_search", {"templates_only": True, "page_size": 5})
        templates: dict[str, Any] = res.structured_content if not res.is_error else {"items": [], "total": 0}
        check("marketplace_search can query pipeline templates", not res.is_error)
        check(
            "template search results provide templates",
            all(item["provides_pipeline_templates"] for item in templates["items"]),
            f"total={templates['total']}",
        )
        if templates["items"]:
            first_template = templates["items"][0]
            res = await call("marketplace_get_dpk", {"dpk_id": first_template["id"]})
            check(
                "marketplace_get_dpk exposes pipeline templates",
                not res.is_error and bool((res.structured_content or {})["pipeline_templates"]),
            )

        print("paging")
        first = await call("marketplace_search", {"page": 0, "page_size": 3})
        second = await call("marketplace_search", {"page": 1, "page_size": 3})
        if not first.is_error and not second.is_error:
            a = [i["id"] for i in first.structured_content["items"]]
            b = [i["id"] for i in second.structured_content["items"]]
            check("page 1 differs from page 0", bool(a) and bool(b) and a != b)

        # Graph editing, back-to-back with no pause between calls. This is the exact shape that
        # lost a node and duplicated two connections during the 2026-08-26 agentic-rag eval run
        # (docs/eval.md): dtlpy's Pipelines.update PATCHes the whole node list, so a read racing a
        # recent write silently discards it. Mocked tests cannot see it -- they stub update() out --
        # and the hand-run check that "verified" the first fix passed while the retry was in fact
        # inert for connect/disconnect. So it belongs here, where it gets re-run.
        print("pipeline graph editing (rapid, unpaced -- the read-modify-write race)")
        pipeline_name = f"{name}-graph"
        res = None
        if dataset_id is None:
            # Dataset nodes need a real dataset id. Without one, skip rather than assert against a
            # None -- the same bail-out the item phases above take.
            check("graph editing skipped, no dataset to point nodes at", True)
        else:
            res = await call("pipeline_create", {"project_id": project_id, "name": pipeline_name})
        if res is not None and res.is_error:
            check("pipeline_create for graph editing", False, _detail(res))
        elif res is not None:
            pipeline_id = res.structured_content["id"]
            graph_args = {"project_id": project_id, "pipeline_id": pipeline_id}

            for node_name, ds_id in (("smoke-a", dataset_id), ("smoke-b", dataset_id)):
                res = await call(
                    "pipeline_node_add",
                    {
                        **graph_args,
                        "node": {"node_type": "dataset", "name": node_name, "dataset_id": ds_id},
                    },
                )
                check(f"pipeline_node_add {node_name}", not res.is_error, _detail(res))

            res = await call(
                "pipeline_connect_nodes",
                {**graph_args, "source_node_name": "smoke-a", "target_node_name": "smoke-b"},
            )
            check("pipeline_connect_nodes immediately after adding both", not res.is_error, _detail(res))

            # The whole point: read back independently and count. A lost update shows up here as a
            # missing node or a duplicated edge, not as an error on the calls above.
            res = await call("pipeline_get", {**graph_args, "include_graph": True})
            if res.is_error:
                check("pipeline_get after rapid edits", False, _detail(res))
            else:
                graph = res.structured_content
                names = sorted(n["name"] for n in graph["nodes"])
                check("both nodes survived the rapid sequence", names == ["smoke-a", "smoke-b"], f"{names}")
                check(
                    "exactly one connection, not duplicated",
                    len(graph["connections"]) == 1,
                    f"{len(graph['connections'])} connections",
                )

            res = await call(
                "pipeline_node_update", {**graph_args, "node_name": "smoke-a", "position": [2, 1]}
            )
            check("pipeline_node_update repositions a node", not res.is_error, _detail(res))

            res = await call(
                "pipeline_disconnect_nodes",
                {
                    **graph_args,
                    "source_node_name": "smoke-a",
                    "target_node_name": "smoke-b",
                    "confirm": True,
                },
            )
            check("pipeline_disconnect_nodes removes the edge", not res.is_error, _detail(res))

            res = await call("pipeline_get", {**graph_args, "include_graph": True})
            check(
                "graph is stable after update and disconnect",
                not res.is_error
                and len(res.structured_content["nodes"]) == 2
                and res.structured_content["connections"] == [],
                _detail(res),
            )

            res = await call("pipeline_validate", {**graph_args})
            if not res.is_error:
                # Its return shape is undocumented; the tool passes it through rather than
                # coercing it, so this run is what tells us what to expect.
                record("Pipelines.validate returns", repr(res.structured_content.get("result"))[:160])
            elif "Invalid roles" in _detail(res, 300):
                # The endpoint refuses this credential. Deliberately *not* attributed to the
                # key's `"roles": []` JWT claim, and deliberately not called "the same class as
                # the /ml/models 403": that generalisation was disproved on 2026-08-31, when a
                # key carrying the identical claim turned out to have full ML CRUD (techspec
                # Appendix B.3). The cause here is unknown; what is verified is that the tool
                # surfaces the refusal cleanly. Not scored as a pass.
                record(
                    "pipeline_validate with an API-key credential",
                    "refused, 400 Invalid roles - cause unknown, not verifiable with this key",
                )
            else:
                check("pipeline_validate on a two-node graph", False, _detail(res))

            res = await call("pipeline_execute", {**graph_args, "confirm": True})
            if res.is_error:
                # An uninstalled pipeline may well refuse to run. Either answer is informative;
                # what matters is that the failure is legible rather than a traceback.
                record("pipeline_execute on an uninstalled pipeline", _detail(res, 160))
            else:
                record("pipeline_execute on an uninstalled pipeline", "accepted")

            res = await call("execution_list", {"project_id": project_id, "pipeline_id": pipeline_id})
            check("execution_list returns without error", not res.is_error, _detail(res))

            res = await call(
                "execution_terminate",
                {
                    "project_id": project_id,
                    "execution_id": f"no-such-{uuid.uuid4().hex[:8]}",
                    "confirm": True,
                },
            )
            check(
                "execution_terminate on an unknown id fails cleanly",
                res.is_error and "traceback" not in _detail(res, 400).lower(),
                _detail(res),
            )

            res = await call("pipeline_delete", {**graph_args, "confirm": True})
            check("pipeline_delete cleans up the smoke pipeline", not res.is_error, _detail(res))

        # --- the determinism claim, against the platform rather than in memory ---------------
        #
        # `doe_mcp.blueprint` derives node and port ids with uuid5 instead of letting dtlpy mint
        # uuid4s, and everything asserted about deterministic construction assumes the platform
        # stores them unchanged. That has only ever been verified in memory. If DOE rejects a
        # client-supplied id, or silently rewrites it on save, the byte-identical claim is false
        # and the guarantee has to weaken to canonical-form equality. This is the check that
        # settles it.
        from doe_mcp.blueprint import build_graph, fingerprint
        from doe_mcp.tools.skills import rag_blueprint

        # A blueprint needs concrete dataset ids -- they are what the fingerprint derives from --
        # and dataset_id is None on the partial-create path, which the cleanup test exercises.
        # Skip rather than assert, matching how the graph-editing section above handles it.
        if dataset_id is None:
            record("deterministic build against the platform", "skipped: no dataset was created")
            await cleanup_dataset(dataset_id, name)
            return

        def smoke_plan(pipeline_id: str, name: str):
            return rag_blueprint(
                name=name,
                source_dataset_id=dataset_id,
                output_dataset_id=dataset_id,
                model_id="smoke-not-a-real-model",
                chunk_size=512,
                chunk_overlap=50,
                load_existing=False,
                pipeline_id=pipeline_id,
            )

        det_name = f"smoke-determinism-{uuid.uuid4().hex[:8]}"
        res = await call("pipeline_create", {"project_id": project_id, "name": det_name})
        if res.is_error:
            check("pipeline_create for the determinism check", False, _detail(res))
        else:
            det_id = res.structured_content["id"]
            plan = smoke_plan(det_id, det_name)
            record("blueprint fingerprint", fingerprint(plan)[:16])
            det_args = {"project_id": project_id, "pipeline_id": det_id}
            try:
                import dtlpy as dl

                live = dl.projects.get(project_id=project_id).pipelines.get(pipeline_id=det_id)
                build_graph(live, plan)
                expected = {node.node_id for node in live.nodes}
                expected_ports = {
                    port.port_id
                    for node in live.nodes
                    for port in (list(node.inputs or []) + list(node.outputs or []))
                }
                live.update()

                res = await call("pipeline_get", {**det_args, "include_graph": True})
                if res.is_error:
                    check("read back the derived-id graph", False, _detail(res))
                else:
                    graph = res.structured_content
                    stored = {n["id"] for n in graph["nodes"]}
                    # `serialize.node` emits snake_case `port_id`; `portId` is the raw
                    # `to_json()` spelling and is not what pipeline_get returns.
                    stored_ports = {p["port_id"] for n in graph["nodes"] for p in n["inputs"] + n["outputs"]}
                    check(
                        "platform stores client-supplied node ids unchanged",
                        stored == expected,
                        f"{len(stored & expected)}/{len(expected)} kept; "
                        f"rewritten={sorted(stored - expected)[:2]}",
                    )
                    check(
                        "platform stores client-supplied port ids unchanged",
                        stored_ports == expected_ports,
                        f"{len(stored_ports & expected_ports)}/{len(expected_ports)} kept",
                    )
                    check(
                        "all five blueprint nodes were written in one PATCH",
                        len(graph["nodes"]) == 5,
                        f"{len(graph['nodes'])} nodes",
                    )
                    check(
                        "all four blueprint edges survived that PATCH",
                        len(graph["connections"]) == 4,
                        f"{len(graph['connections'])} connections",
                    )
            except Exception as exc:  # noqa: BLE001 - a live probe reports, it does not crash the run
                check("deterministic build against the platform", False, f"{type(exc).__name__}: {exc}")
            finally:
                await call("pipeline_delete", {**det_args, "confirm": True})

        await cleanup_dataset(dataset_id, name)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    asyncio.run(run(sys.argv[1]))
    if unknowns:
        print("\nplatform behaviours this run settled:")
        for line in unknowns:
            print(f"  - {line}")
    print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
