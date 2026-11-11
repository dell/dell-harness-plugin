# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Structured MCP output contracts.

The Dataloop SDK returns rich entity objects whose complete JSON is both unstable and
expensive to put in a model's context.  Tools instead return these deliberately small,
typed summaries.  Stable ids are present wherever a later tool call can address the
entity.
"""

from typing import Any, NotRequired

from typing_extensions import TypedDict


class ProjectSummary(TypedDict):
    id: str | None
    name: str | None
    created_at: str | int | float | None
    creator: str | None
    role: str | None
    archived: bool | None


class DatasetSummary(TypedDict):
    id: str | None
    name: str | None
    items_count: int | None
    annotations_count: int | None
    readonly: bool | None
    index_driver: str | None
    creator: str | None
    created_at: str | int | float | None
    updated_at: str | int | float | None
    url: str | None


class PortSummary(TypedDict):
    port_id: str | None
    name: str | None
    type: str | None


class NodeSummary(TypedDict):
    id: str | None
    name: str | None
    type: str | None
    inputs: list[PortSummary]
    outputs: list[PortSummary]


class ConnectionEndpoint(TypedDict):
    node_id: str | None
    port_id: str | None


class ConnectionSummary(TypedDict):
    source: ConnectionEndpoint
    target: ConnectionEndpoint
    action: str | None


class PipelineStatusSummary(TypedDict):
    id: str | None
    name: str | None
    status: str | None
    project_id: str | None
    description: str | None
    created_at: str | int | float | None
    updated_at: str | int | float | None
    node_count: int
    url: str | None


class PipelineSummary(PipelineStatusSummary, total=False):
    nodes: list[NodeSummary] | None
    connections: list[ConnectionSummary] | None
    start_nodes: list[Any] | None


class PipelineNodeResult(TypedDict):
    pipeline: PipelineStatusSummary
    node: NodeSummary


class PipelineTemplateSummary(TypedDict):
    name: str | None
    description: str | None
    node_count: int
    node_names: list[str | None]
    node_types: list[str]


class DpkSummaryBase(TypedDict):
    id: str | None
    base_id: str | None
    name: str | None
    display_name: str | None
    version: str | None
    description: str | None
    scope: str | None
    attributes: Any
    tags: Any
    creator: str | None
    provides_models: bool
    provides_pipeline_templates: bool


class DpkSummary(DpkSummaryBase, total=False):
    models: list[dict[str, Any]] | None
    pipeline_templates: list[PipelineTemplateSummary] | None
    latest_version: str | None


class AppSummary(TypedDict):
    id: str | None
    name: str | None
    status: str | None
    project_id: str | None
    dpk_name: str | None
    dpk_version: str | None
    scope: str | None


class InstalledAppSummary(AppSummary):
    available_model_components: list[str | None]


class ModelSummary(TypedDict):
    id: str | None
    name: str | None
    status: str | None
    version: str | None
    project_id: str | None
    dataset_id: str | None
    input_type: str | None
    output_type: str | None
    labels: Any
    tags: Any
    description: str | None
    module_name: str | None
    url: str | None


class ItemSummary(TypedDict):
    id: str | None
    name: str | None
    dir: str | None
    type: str | None
    mimetype: str | None
    filename: str | None
    dataset_id: str | None
    annotated: bool | None
    annotations_count: int | None
    created_at: str | int | float | None
    url: str | None


class AnnotationSummary(TypedDict):
    id: str | None
    item_id: str | None
    label: str | None
    type: str | None
    creator: str | None
    created_at: str | int | float | None
    model_id: str | None
    model_name: str | None
    confidence: float | None
    coordinates: Any


class ServiceSummary(TypedDict):
    id: str | None
    name: str | None
    active: bool | None
    module_name: str | None
    package_id: str | None
    project_id: str | None
    url: str | None


class DatasetScanResult(TypedDict):
    scanned: int
    scan_limit: int
    scan_truncated: bool


class DatasetProfileDataset(TypedDict):
    id: str
    name: str | None
    items_count: int | None
    annotations_count: int | None


class DatasetProfileResult(TypedDict):
    dataset: DatasetProfileDataset
    file_types: dict[str, int]
    item_kinds: dict[str, int]
    directories: dict[str, int]
    annotated_items: int
    unannotated_items: int
    sample_filenames: list[str | None]
    scanned: int
    scan_limit: int
    scan_truncated: bool
    caveat: str | None


class AnnotationLabelsResult(TypedDict):
    dataset_id: str
    labels: list[str]
    count: int


class ModelDeployResult(TypedDict):
    model_id: str
    service: ServiceSummary


class ModelPredictionResult(TypedDict):
    model_id: str
    model_status: str | None
    scope: str
    execution_id: str | None
    note: str


class ModelSubsetResult(TypedDict):
    model_id: str
    subset: str
    matched_items: int | None
    pii_excluded: bool
    # The exact filter clause applied, so the exclusion can be audited.
    pii_exclusion_filter: dict[str, Any] | None
    warning: str | None


class ModelSubsetRemovalResult(TypedDict):
    model_id: str
    subset: str
    removed: bool


class DeleteResult(TypedDict):
    deleted: bool
    id: str
    name: str | None


class AppUninstallResult(TypedDict):
    uninstalled: bool
    id: str


class PiiReportSample(TypedDict):
    # Allowlisted on purpose: item names and metadata can themselves contain PII.
    item_id: str
    matched_entity_types: NotRequired[list[str]]


class PiiReportResult(TypedDict):
    dataset_id: str
    verdict: str
    flagged_items: int
    flagged_count_is_exact: bool
    dataset_items: int | None
    marker: dict[str, Any]
    sample: list[PiiReportSample]
    caveat: str | None


class PiiFindingsWriteResult(TypedDict):
    item_id: str
    written_to: str
    detected_entity_types: list[str]
    findings: dict[str, bool]
    marker_hint: dict[str, list[str]] | None


class RagQueryMatch(TypedDict):
    item_id: str
    item_name: str | None
    similarity: float
    source_item_id: str | None
    source_item_name: str | None
    chunk_index: int | None
    text: str | None


class RagQueryResult(TypedDict):
    query: str
    dataset_id: str
    feature_set_id: str
    # Proof the temporary query item and its vector were deleted after embedding.
    query_cleanup: dict[str, Any]
    vectors_scanned: int
    scan_truncated: bool
    results_count: int
    results: list[RagQueryMatch]


class PipelineExecutionSummary(TypedDict):
    id: str | None
    pipeline_id: str | None
    status: str | None
    created_at: str | int | float | None
    updated_at: str | int | float | None
    nodes: dict[str, Any]


class PipelineExecutionResult(TypedDict):
    execution: PipelineExecutionSummary


class ExecutionSummary(TypedDict):
    id: str | None
    status: str | None
    in_progress: bool | None
    service_id: str | None
    function_name: str | None
    pipeline_id: str | None
    node_id: str | None
    created_at: str | int | float | None
    updated_at: str | int | float | None


class ExecutionTerminationResult(TypedDict):
    terminated: bool
    execution: ExecutionSummary


class PipelineValidationResult(TypedDict):
    pipeline_id: str
    result: Any


class PipelineNodeRemovalResult(TypedDict):
    removed: bool
    node_name: str
    pipeline: PipelineStatusSummary
