#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Live retest of the second OpenAI review round against a real DOE instance.

Every resource is created inside a new, uniquely named project, and `cleanup` deletes that
project. Credentials come from the environment (DATALOOP_API_KEY, DATALOOP_GATE_URL,
DATALOOP_VERIFY_SSL, DATALOOP_PATH) and are never printed.

    python scripts/live_review_round2.py setup STATE.json      # project + datasets + item
    python scripts/live_review_round2.py ownership STATE.json  # cross-dataset reads/writes
    python scripts/live_review_round2.py pipelines STATE.json  # two pipelines, one output
    python scripts/live_review_round2.py unchanged STATE.json  # item B metadata untouched
    python scripts/live_review_round2.py cleanup STATE.json
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import uuid
from pathlib import Path
from typing import Any

import dtlpy as dl
from mcp import Client

from doe_mcp.auth import ensure_authenticated
from doe_mcp.server import build_server

ORIGINAL_METADATA = {"owner": "dataset-b-team", "classification": "internal"}


def save(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def call(tool: str, arguments: dict[str, Any]) -> tuple[bool, str, Any]:
    async def run():
        async with Client(build_server()) as client:
            return await client.call_tool(tool, arguments)

    result = asyncio.run(run())
    return bool(result.is_error), (result.content[0].text if result.content else ""), result.structured_content


def setup(path: Path) -> dict[str, Any]:
    ensure_authenticated()
    name = f"codex-review-r2-{uuid.uuid4().hex[:8]}"
    project = dl.projects.create(project_name=name)
    state: dict[str, Any] = {"project_id": project.id, "project_name": name}
    save(path, state)
    for key in ("dataset_a", "dataset_b", "rag_source", "rag_output"):
        state[key] = project.datasets.create(dataset_name=f"{key}-{uuid.uuid4().hex[:6]}").id
        save(path, state)
    dataset_b = project.datasets.get(dataset_id=state["dataset_b"])
    item = dataset_b.items.upload(
        local_path=io.BytesIO(b"dataset B private note"), remote_name="b-private.txt",
        item_metadata={"user": dict(ORIGINAL_METADATA)},
    )
    state["item_b"] = item.id
    save(path, state)
    return state


def ownership(state: dict[str, Any]) -> dict[str, Any]:
    p, a, b, item = state["project_id"], state["dataset_a"], state["dataset_b"], state["item_b"]
    attempts = {
        "item_get": {"project_id": p, "dataset_id": a, "item_id": item},
        "item_update": {"project_id": p, "dataset_id": a, "item_id": item, "metadata": {"owner": "attacker"}},
        "pii_set_findings": {"project_id": p, "dataset_id": a, "item_id": item,
                             "findings": {"entity_types": ["US_SSN"]}},
        "annotation_list": {"project_id": p, "dataset_id": a, "item_id": item},
    }
    results = {}
    for tool, arguments in attempts.items():
        is_error, text, content = call(tool, arguments)
        results[tool] = {
            "rejected": is_error and f"belongs to dataset '{b}'" in text,
            "leaked_name": "b-private" in text or "b-private" in json.dumps(content or {}),
        }
    is_error, _, content = call("item_get", {"project_id": p, "dataset_id": b, "item_id": item})
    results["control_same_dataset_item_get"] = {"served": not is_error and content["id"] == item}
    results["item_b_metadata_unchanged"] = unchanged(state)
    return results


def unchanged(state: dict[str, Any]) -> bool:
    ensure_authenticated()
    metadata = dl.items.get(item_id=state["item_b"]).metadata or {}
    user = metadata.get("user", {})
    return {key: user.get(key) for key in ORIGINAL_METADATA} == ORIGINAL_METADATA and "pii" not in user


def deployed_function(project_id: str, pipeline_id: str, node_name: str):
    """The exact code the platform stores for a node, loaded so it can run against live data."""
    pipeline = dl.pipelines.get(pipeline_id=pipeline_id)
    node = next(n for n in pipeline.nodes if n.name == node_name)
    namespace: dict[str, Any] = {}
    exec(compile(node.config["package"]["code"], f"<{pipeline_id}:{node_name}>", "exec"), namespace)  # noqa: S102
    return namespace["ServiceRunner"].run


def pipelines(state: dict[str, Any], path: Path) -> dict[str, Any]:
    p, source, output = state["project_id"], state["rag_source"], state["rag_output"]
    functions = {}
    for label, chunk_size in (("a", 100), ("b", 400)):
        is_error, text, content = call("rag_pipeline_create", {
            "project_id": p, "name": f"rag-{label}-{uuid.uuid4().hex[:6]}",
            "source_dataset_id": source, "output_dataset_id": output,
        })
        assert not is_error, text
        pipeline_id = content["id"]
        state[f"pipeline_{label}"] = pipeline_id
        save(path, state)
        is_error, text, _ = call("rag_add_text_to_chunks_node", {
            "project_id": p, "pipeline_id": pipeline_id, "output_dataset_id": output,
            "chunk_size": chunk_size, "chunk_overlap": 0, "node_name": "text_to_chunks",
        })
        assert not is_error, text
        functions[label] = deployed_function(p, pipeline_id, "text_to_chunks")

    ensure_authenticated()
    output_dataset = dl.datasets.get(dataset_id=output)
    text_item = output_dataset.items.upload(
        local_path=io.BytesIO(("A" * 350).encode()), remote_path="/inputs", remote_name="handbook.txt",
        item_metadata={"user": {"rag": {"role": "text", "source_item_id": "pdf-shared",
                                        "source_item_name": "handbook.pdf"}}},
    )

    def chunks(pipeline_id: str) -> dict[str, str]:
        filters = dl.Filters(resource=dl.FiltersResource.ITEM)
        filters.add(field="metadata.user.rag.role", values="chunk")
        filters.add(field="metadata.user.rag.pipeline_id", values=pipeline_id)
        return {i.id: i.filename for i in output_dataset.items.list(filters=filters).all()}

    functions["a"](dl.items.get(item_id=text_item.id))
    a_before = chunks(state["pipeline_a"])
    functions["b"](dl.items.get(item_id=text_item.id))
    a_after, b_after = chunks(state["pipeline_a"]), chunks(state["pipeline_b"])
    return {
        "pipeline_a_chunks_before": len(a_before),
        "pipeline_a_chunks_after_b_ran": len(a_after),
        "pipeline_a_items_identical": a_before == a_after,
        "pipeline_b_chunks": len(b_after),
        "no_shared_items": not set(a_after) & set(b_after),
        "folders": sorted({name.rsplit("/", 1)[0] for name in {**a_after, **b_after}.values()}),
    }


def cleanup(state: dict[str, Any]) -> dict[str, Any]:
    ensure_authenticated()
    project = dl.projects.get(project_id=state["project_id"])
    if project.name != state["project_name"] or not project.name.startswith("codex-review-r2-"):
        raise SystemExit("refusing to delete a project this script did not create")
    project.delete(True, True)
    return {"deleted_project": state["project_name"]}


def main() -> int:
    command, path = sys.argv[1], Path(sys.argv[2])
    state = json.loads(path.read_text()) if path.exists() else {}
    if command == "setup":
        out: Any = {k: v for k, v in setup(path).items()}
    elif command == "ownership":
        out = ownership(state)
    elif command == "pipelines":
        out = pipelines(state, path)
    elif command == "unchanged":
        out = {"item_b_metadata_unchanged": unchanged(state)}
    elif command == "cleanup":
        out = cleanup(state)
    else:
        raise SystemExit(__doc__)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
