"""Compact entity summaries.

A dtlpy ``to_json()`` carries far more than a model needs and costs a lot of context, so
each entity is reduced to the fields a caller acts on. Ids are always present, because
every follow-up tool call is addressed by id.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from enum import Enum
from typing import Any

import dtlpy as dl

from . import outputs

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]

_NOT_ENUM_LIKE = object()


def json_safe(value: Any) -> JsonValue:
    """Normalize dtlpy values into MCP structured-content primitives."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return json_safe(value.value)
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [json_safe(item) for item in value]
    # Some dtlpy objects expose computed properties that raise when read. Treat
    # enum-like ``.value`` objects consistently without letting one unsafe
    # property turn a successful platform operation into a serialization error.
    try:
        inner = value.value if hasattr(value, "value") else _NOT_ENUM_LIKE
    except Exception:
        return str(value)
    if inner is not _NOT_ENUM_LIKE:
        return json_safe(inner)
    return str(value)


def _summary(values: dict[str, Any]) -> Any:
    return {key: json_safe(value) for key, value in values.items()}


def optional(getter: Callable[[], Any]) -> Any:
    """Read a dtlpy property that computes its value and may fail.

    Not every attribute on an entity is a stored field. ``Item.platform_url`` builds a URL
    through the client's environment and raises ``BadRequest('400', 'Unknown environment')``
    when that context is missing — so a summary that reads it directly turns a perfectly good
    entity into an exception. Live smoke caught this: every uploaded item was reported as a
    failure because summarising it raised after the upload had already succeeded.
    """
    try:
        return getter()
    except Exception:
        return None


def project(entity: dl.entities.Project) -> Any:
    return _summary(
        {
            "id": entity.id,
            "name": entity.name,
            "created_at": entity.created_at,
            "creator": entity.creator,
            "role": entity.role,
            "archived": entity.archived,
        }
    )


def dataset(entity: dl.entities.Dataset) -> Any:
    return _summary(
        {
            "id": entity.id,
            "name": entity.name,
            "items_count": entity.items_count,
            "annotations_count": entity.annotations_count,
            "readonly": entity.readonly,
            "index_driver": entity.index_driver,
            "creator": entity.creator,
            "created_at": entity.created_at,
            "updated_at": entity.updated_at,
            "url": entity.url,
        }
    )


def node(entity: dl.entities.PipelineNode) -> Any:
    return _summary(
        {
            "id": entity.node_id,
            "name": entity.name,
            "type": entity.node_type,
            "inputs": [
                {"port_id": io.port_id, "name": io.name, "type": io.input_type}
                for io in (getattr(entity, "inputs", None) or [])
            ],
            "outputs": [
                {"port_id": io.port_id, "name": io.name, "type": io.input_type}
                for io in (getattr(entity, "outputs", None) or [])
            ],
        }
    )


def connection(entity: dl.entities.PipelineConnection) -> Any:
    return _summary(
        {
            "source": {"node_id": entity.source.node_id, "port_id": entity.source.port_id},
            "target": {"node_id": entity.target.node_id, "port_id": entity.target.port_id},
            "action": entity.action,
        }
    )


def pipeline(entity: dl.entities.Pipeline, include_graph: bool = False) -> Any:
    nodes = getattr(entity, "nodes", None) or []
    connections = getattr(entity, "connections", None) or []
    summary: dict[str, Any] = {
        "id": entity.id,
        "name": entity.name,
        "status": entity.status,
        "project_id": entity.project_id,
        "description": entity.description,
        "created_at": entity.created_at,
        "updated_at": entity.updated_at,
        "node_count": len(nodes),
        "url": entity.url,
    }
    if include_graph:
        summary["nodes"] = [node(n) for n in nodes]
        summary["connections"] = [connection(c) for c in connections]
        summary["start_nodes"] = getattr(entity, "start_nodes", [])
    return _summary(summary)


def dpk(entity: dl.entities.Dpk, include_components: bool = False) -> Any:
    components = getattr(entity, "components", None)
    summary: dict[str, Any] = {
        "id": entity.id,
        "base_id": entity.base_id,
        "name": entity.name,
        "display_name": entity.display_name,
        "version": entity.version,
        "description": entity.description,
        "scope": entity.scope,
        "attributes": entity.attributes,
        "tags": entity.tags,
        "creator": entity.creator,
        "provides_models": bool(getattr(components, "models", None)),
        # Whether pipeline_create_from_template can instantiate a pipeline from this
        # package. Template-only DPKs carry no model components at all.
        "provides_pipeline_templates": bool(getattr(components, "pipeline_templates", None)),
    }
    if include_components:
        summary["models"] = [
            {
                "name": model.get("name"),
                "description": model.get("description"),
                "module_name": model.get("moduleName"),
                "status": model.get("status"),
                "labels_count": len(model.get("labels") or []),
                "configuration": model.get("configuration"),
            }
            for model in (getattr(components, "models", None) or [])
        ]
        # The template `name` values here are what pipeline_create_from_template takes as
        # template_name.
        summary["pipeline_templates"] = [
            pipeline_template(template)
            for template in (getattr(components, "pipeline_templates", None) or [])
        ]
        summary["latest_version"] = entity.version
    return _summary(summary)


def pipeline_template(template: dict[str, Any]) -> Any:
    """Summarize one DPK pipeline template without dumping its whole graph."""
    nodes = template.get("nodes") or []
    return _summary(
        {
            "name": template.get("name"),
            "description": template.get("description"),
            "node_count": len(nodes),
            "node_names": [node.get("name") for node in nodes if isinstance(node, dict)],
            "node_types": sorted(
                {node.get("type") for node in nodes if isinstance(node, dict) and node.get("type")}
            ),
        }
    )


def app(entity: dl.entities.App) -> Any:
    return _summary(
        {
            "id": entity.id,
            "name": entity.name,
            "status": entity.status,
            "project_id": entity.project_id,
            "dpk_name": entity.dpk_name,
            "dpk_version": entity.dpk_version,
            "scope": entity.scope,
        }
    )


def model(entity: dl.entities.Model) -> Any:
    return _summary(
        {
            "id": entity.id,
            "name": entity.name,
            "status": entity.status,
            "version": entity.version,
            "project_id": entity.project_id,
            "dataset_id": entity.dataset_id,
            "input_type": entity.input_type,
            "output_type": entity.output_type,
            "labels": entity.labels,
            "tags": entity.tags,
            "description": entity.description,
            "module_name": entity.module_name,
            "url": entity.url,
        }
    )


def item(entity: dl.entities.Item) -> Any:
    # `metadata` carries the platform's own `system` block alongside user data; only the user
    # half is echoed back, so a caller never round-trips platform internals into a patch.
    metadata = getattr(entity, "metadata", None) or {}
    user_metadata = {key: value for key, value in metadata.items() if key != "system"}
    return _summary(
        {
            "id": entity.id,
            "name": entity.name,
            "dir": entity.dir,
            "type": entity.type,
            "mimetype": entity.mimetype,
            "filename": entity.filename,
            "dataset_id": entity.dataset_id,
            "annotated": entity.annotated,
            "annotations_count": getattr(entity, "annotations_count", None),
            "created_at": entity.created_at,
            "metadata": user_metadata,
            # Computed by the SDK and raises without client environment context.
            "url": optional(lambda: entity.platform_url),
        }
    )


def annotation(entity: dl.entities.Annotation) -> Any:
    # dtlpy records model attribution under metadata.user.model as
    # {'name', 'model_id', 'confidence'} — see AnnotationCollection.add(model_info=...).
    # Surfacing it is what makes "delete everything this model wrote" reviewable.
    model = ((getattr(entity, "metadata", None) or {}).get("user") or {}).get("model") or {}
    return _summary(
        {
            "id": entity.id,
            "item_id": entity.item_id,
            "label": entity.label,
            "type": entity.type,
            "creator": entity.creator,
            "created_at": entity.created_at,
            "model_id": model.get("model_id"),
            "model_name": model.get("name"),
            "confidence": model.get("confidence"),
            # Also computed on some annotation types.
            "coordinates": optional(lambda: entity.coordinates),
        }
    )


def trigger(entity: dl.entities.BaseTrigger) -> Any:
    return _summary(
        {
            "id": entity.id,
            "name": entity.name,
            "active": entity.active,
            "type": getattr(entity, "type", None),
            "resource": getattr(entity, "resource", None),
            "actions": getattr(entity, "actions", None),
            "execution_mode": getattr(entity, "execution_mode", None),
            "service_id": getattr(entity, "service_id", None),
            "pipeline_id": getattr(entity, "pipeline_id", None),
            "function_name": getattr(entity, "function_name", None),
            "project_id": getattr(entity, "project_id", None),
            "created_at": getattr(entity, "createdAt", None),
        }
    )


def service(entity: dl.entities.Service) -> Any:
    return _summary(
        {
            "id": entity.id,
            "name": entity.name,
            "active": entity.active,
            "module_name": entity.module_name,
            "package_id": entity.package_id,
            "project_id": entity.project_id,
            "url": entity.url,
        }
    )


def pipeline_execution(entity: Any) -> outputs.PipelineExecutionSummary:
    """One run of a whole pipeline."""
    return _summary(
        {
            "id": getattr(entity, "id", None),
            "pipeline_id": getattr(entity, "pipeline_id", None),
            "status": getattr(entity, "status", None),
            "created_at": getattr(entity, "created_at", None),
            "updated_at": getattr(entity, "updated_at", None),
            # Per-node progress is the whole reason to look at a pipeline execution, but it is a
            # nested blob; reduced to node -> status so a stalled stage is visible without dumping it.
            "nodes": {
                str(node.get("nodeId")): node.get("status")
                for node in (getattr(entity, "nodes", None) or [])
                if isinstance(node, dict)
            },
        }
    )


def execution(entity: Any) -> outputs.ExecutionSummary:
    """One run of a single service function, the unit that actually gets stuck."""
    return _summary(
        {
            "id": getattr(entity, "id", None),
            "status": getattr(entity, "status", None),
            "in_progress": getattr(entity, "in_progress", None),
            "service_id": getattr(entity, "service_id", None),
            "function_name": getattr(entity, "function_name", None),
            "pipeline_id": getattr(entity, "pipeline_id", None),
            "node_id": getattr(entity, "node_id", None),
            "created_at": getattr(entity, "created_at", None),
            "updated_at": getattr(entity, "updated_at", None),
        }
    )
