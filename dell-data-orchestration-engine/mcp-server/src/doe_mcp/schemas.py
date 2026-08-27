"""Tool input models for pipeline nodes.

Each node type in dtlpy takes a different, largely non-overlapping set of required
arguments, so a single free-form dict would push the validation burden onto the model. A
discriminated union on ``node_type`` means the SDK advertises one schema per node type and
rejects a malformed node before any platform call is made.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from . import code_node, dql

Position = Annotated[tuple[int, int], Field(description="Grid position (x, y) in the pipeline editor.")]


class BaseNodeSpec(BaseModel):
    name: str = Field(min_length=1, description="Display name, unique within the pipeline.")
    position: Position = (1, 1)


class DatasetNodeSpec(BaseNodeSpec):
    """A dataset (storage) node: items flow into or out of a dataset."""

    node_type: Literal["dataset"]
    dataset_id: str = Field(description="Dataset this node reads from or writes to.")
    dataset_folder: str | None = Field(default=None, description="Folder to work in, e.g. '/incoming'.")
    load_existing_data: bool = Field(
        default=False, description="On install, feed the dataset's existing items into the pipeline."
    )
    data_filters: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Raw DQL query scoping which existing items load_existing_data feeds in, e.g. "
            "{'filter': {'$and': [{'metadata.system.mimetype': {'$eq': 'application/pdf'}}]}}. "
            "Only applies when load_existing_data is true."
        ),
    )

    @field_validator("data_filters")
    @classmethod
    def _validate_data_filters(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        """Reject a malformed DQL query before any platform call, not after."""
        if value is not None:
            dql.validate_data_filters(value)
        return value


class CodeNodeSpec(BaseNodeSpec):
    """A code node: a Python function that DOE runs on each item."""

    node_type: Literal["code"]
    code: str = Field(
        description=(
            "Exactly one top-level function and nothing else; put imports inside it. Takes an "
            "item, returns one, e.g. 'def run(item):\\n    return item'. Runs on DOE."
        )
    )

    @field_validator("code")
    @classmethod
    def _validate_code(cls, value: str) -> str:
        """Reject unusable source before any platform call, not after."""
        code_node.function_name_of(value)
        return value


class FunctionNodeSpec(BaseNodeSpec):
    """A function node: invokes a function of an already deployed service."""

    node_type: Literal["function"]
    service_id: str = Field(description="Id of the deployed service to invoke.")
    function_name: str = Field(description="Function name on that service's module.")


class WorkloadUnitSpec(BaseModel):
    """One annotator's share of a task's items."""

    assignee_id: str = Field(description="Email of the assignee.")
    load: float = Field(default=0, ge=0, le=100, description="Percentage of items assigned.")


class TaskNodeSpec(BaseNodeSpec):
    """A task node: routes items into an annotation or QA task."""

    node_type: Literal["task"]
    dataset_id: str = Field(description="Dataset the task draws items from.")
    task_owner: str = Field(description="Email of the task owner.")
    workload: list[WorkloadUnitSpec] = Field(min_length=1, description="Assignees and their share.")
    task_type: Literal["annotation", "qa"] = Field(default="annotation", description="Kind of task.")
    recipe_id: str | None = Field(default=None, description="Defaults to the dataset's own recipe.")
    recipe_title: str | None = Field(default=None, description="Title of that recipe.")


NodeSpec = Annotated[
    DatasetNodeSpec | CodeNodeSpec | FunctionNodeSpec | TaskNodeSpec,
    Field(discriminator="node_type"),
]


class PiiFindingsSpec(BaseModel):
    """What a PII scan found on one item.

    Written to `metadata.user.pii` as a flat map of detected entity type to `true` — e.g.
    ``{"US_SSN": true, "EMAIL_ADDRESS": true}``. Nothing else goes in that map: no counts, no
    scores, and above all none of the matched strings. The map answers "which kinds of PII are
    in here", which is what routing and exclusion need; the values themselves are the PII, and
    copying them into metadata would put them somewhere more queryable and more exportable than
    the document they came from.

    An entity type that was not detected is **absent**, not `false`. The map's presence is what
    records that a scan ran, so a scanned-clean item carries an empty map.
    """

    entity_types: list[str] = Field(
        default_factory=list,
        description=(
            "Entity types detected on this item, e.g. ['US_SSN', 'EMAIL_ADDRESS']. "
            "Each becomes a `<TYPE>: true` pair. Empty means scanned and nothing found."
        ),
    )

    def as_metadata(self) -> dict[str, bool]:
        """The flat map written under `metadata.user.pii`."""
        return {entity: True for entity in sorted(set(self.entity_types))}


class PiiMarkerSpec(BaseModel):
    """How a PII scan records its verdict on an item. Caller-supplied; exactly one shape required."""

    entity_types: list[str] | None = Field(
        default=None,
        description=(
            "Entity types counting as PII, e.g. ['US_SSN', 'CREDIT_CARD']. Matches metadata.user.pii "
            "— the shape this server's own writeback produces."
        ),
    )
    annotation_label: str | None = Field(
        default=None, description="Annotation label carried when PII is found, e.g. 'pii'."
    )
    metadata_field: str | None = Field(
        default=None, description="User metadata field another scanner sets, e.g. 'pii_detected'."
    )
    metadata_value: Any = Field(default=True, description="Value that field takes when found. Default true.")

    @model_validator(mode="after")
    def exactly_one_shape(self) -> "PiiMarkerSpec":
        shapes = [bool(self.entity_types), bool(self.annotation_label), bool(self.metadata_field)]
        if sum(shapes) != 1:
            raise ValueError(
                "Provide exactly one of entity_types, annotation_label or metadata_field. "
                "Guessing between them would silently report 'no PII' on every dataset."
            )
        return self
