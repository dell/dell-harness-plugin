"""Dataset CRUD."""

import hashlib
from typing import Annotated, Any

import dtlpy as dl
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from .. import outputs, paging, serialize
from ..context import get_dataset, get_project
from ..guards import confirm_or_elicit

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
MUTATING = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False
)
SAFE_PATCH_FIELDS = {"name", "description", "metadata"}


def validate_dataset_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Accept only the dataset fields this tool deliberately exposes."""
    unknown = sorted(set(patch) - SAFE_PATCH_FIELDS)
    if unknown:
        allowed = ", ".join(sorted(SAFE_PATCH_FIELDS))
        rejected = ", ".join(unknown)
        raise ValueError(f"Unsupported dataset patch fields: {rejected}. Allowed fields: {allowed}.")
    return dict(patch)


def color_for(tag: str) -> tuple[int, int, int]:
    """Pick a stable RGB colour for a label name.

    A colour is mandatory, not cosmetic: ``Label.to_root`` omits the ``color`` key when the
    label has none, but ``Ontologies.labels_to_roots`` then reads ``root['value']['color']``
    unconditionally and dies with ``KeyError: 'color'``. dtlpy's own ``ontology.add_label``
    solves this with ``random.randint``; hashing the tag instead keeps a given label the
    same colour on every run, which matters when a dataset is recreated.
    """
    digest = hashlib.md5(tag.encode(), usedforsecurity=False).digest()
    return digest[0], digest[1], digest[2]


def build_labels(labels: list[str] | dict[str, str] | None) -> list[dl.entities.Label] | None:
    """Turn label names (or name -> colour pairs) into ``Label`` entities.

    dtlpy's ``datasets.create`` cannot actually accept the plain shapes its signature
    suggests: it wraps a non-list in a list and then calls ``serialize_labels``, which
    needs a mapping, so both ``['cat']`` and ``{'cat': (255, 0, 0)}`` raise
    ``AttributeError``. A list of ``Label`` entities is the only input that survives, so
    the conversion happens here.
    """
    if labels is None:
        return None
    if isinstance(labels, dict):
        return [dl.entities.Label(tag=tag, color=color or color_for(tag)) for tag, color in labels.items()]
    return [dl.entities.Label(tag=tag, color=color_for(tag)) for tag in labels]


def register(mcp: MCPServer) -> None:
    @mcp.tool(title="List datasets", annotations=READ_ONLY)
    def dataset_list(
        project_id: Annotated[str, Field(description="Project whose datasets to list.")],
        name: Annotated[str | None, Field(description="Filter by exact dataset name.")] = None,
        page: Annotated[int, Field(ge=0, description="Zero-based page number.")] = 0,
        page_size: Annotated[int, Field(ge=1, le=paging.MAX_LIMIT, description="Datasets per page.")] = (
            paging.DEFAULT_LIMIT
        ),
        limit: Annotated[int, Field(ge=1, le=paging.MAX_LIMIT, description="Max datasets to return.")] = (
            paging.DEFAULT_LIMIT
        ),
        fields: Annotated[list[str] | None, Field(description=paging.FIELDS_DESCRIPTION)] = None,
    ) -> paging.ListResult:
        """List datasets in a project."""
        project = get_project(project_id)
        # datasets.list() ignores paging and returns every dataset, so page client-side.
        return paging.from_list(
            project.datasets.list(name=name),
            serialize.dataset,
            page=page,
            page_size=page_size,
            limit=limit,
            fields=fields,
        )

    @mcp.tool(title="Get dataset", annotations=READ_ONLY)
    def dataset_get(
        project_id: Annotated[str, Field(description="Project that owns the dataset.")],
        dataset_id: Annotated[str, Field(description="Id of the dataset to fetch.")],
    ) -> outputs.DatasetSummary:
        """Fetch one dataset by id."""
        return serialize.dataset(get_dataset(project_id, dataset_id))

    @mcp.tool(title="Create dataset", annotations=MUTATING)
    def dataset_create(
        project_id: Annotated[str, Field(description="Project to create the dataset in.")],
        name: Annotated[str, Field(min_length=1, description="Name for the new dataset.")],
        labels: Annotated[
            list[str] | dict[str, str] | None,
            Field(
                description=(
                    "Ontology labels: either a list of label names, or a mapping of label "
                    "name to hex colour such as {'cat': '#ff0000'}."
                )
            ),
        ] = None,
        ontology_ids: Annotated[
            list[str] | None, Field(description="Attach existing ontologies instead of creating labels.")
        ] = None,
        recipe_id: Annotated[str | None, Field(description="Attach an existing recipe.")] = None,
        driver_id: Annotated[
            str | None, Field(description="Storage driver id, for datasets backed by external storage.")
        ] = None,
    ) -> dict[str, Any]:
        """Create a dataset.

        With no labels, ontology or recipe, Dataloop attaches a default recipe.
        """
        project = get_project(project_id)
        dataset = project.datasets.create(
            dataset_name=name,
            labels=build_labels(labels),
            ontology_ids=ontology_ids,
            recipe_id=recipe_id,
            driver_id=driver_id,
        )
        return serialize.dataset(dataset)

    @mcp.tool(title="Update dataset", annotations=MUTATING)
    def dataset_update(
        project_id: Annotated[str, Field(description="Project that owns the dataset.")],
        dataset_id: Annotated[str, Field(description="Id of the dataset to update.")],
        name: Annotated[str | None, Field(description="New name for the dataset.")] = None,
        patch: Annotated[
            dict[str, Any] | None,
            Field(description="Patch body for supported fields only: description and metadata."),
        ] = None,
    ) -> dict[str, Any]:
        """Rename a dataset, or update supported dataset fields."""
        if name is None and patch is None:
            raise ValueError("Provide `name`, `patch`, or both: nothing to update otherwise.")
        dataset = get_dataset(project_id, dataset_id)
        merged_patch = validate_dataset_patch(patch or {})
        if name is not None:
            merged_patch["name"] = name
            dataset.name = name
        updated = dataset.project.datasets.update(dataset=dataset, patch=merged_patch)
        return serialize.dataset(updated)

    @mcp.tool(title="Delete dataset", annotations=DESTRUCTIVE)
    def dataset_delete(
        project_id: Annotated[str, Field(description="Project that owns the dataset.")],
        dataset_id: Annotated[str, Field(description="Id of the dataset to delete.")],
        confirm: Annotated[
            bool,
            Field(
                description=(
                    "Must be true to proceed. Deleting a dataset destroys its items and "
                    "annotations and cannot be undone, so confirm with the user first."
                )
            ),
        ] = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Permanently delete a dataset, including all of its items and annotations."""
        confirm_or_elicit(
            ctx,
            confirm,
            "delete this dataset",
            "it destroys the dataset and everything in it, and cannot be undone",
        )
        dataset = get_dataset(project_id, dataset_id)
        name = dataset.name
        dataset.project.datasets.delete(dataset_id=dataset_id, sure=True, really=True)
        return {"deleted": True, "id": dataset_id, "name": name}

    @mcp.resource(
        "dataloop://projects/{project_id}/datasets/{dataset_id}",
        title="Dataset",
        description="Full dataset detail — the same body dataset_get returns, addressable "
        "outside a tool call so it does not have to round-trip through the conversation.",
        mime_type="application/json",
    )
    def dataset_resource(project_id: str, dataset_id: str) -> dict[str, Any]:
        return serialize.dataset(get_dataset(project_id, dataset_id))
