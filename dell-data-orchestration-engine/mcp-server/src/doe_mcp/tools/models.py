# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Installing marketplace models and bringing them up as services.

Getting a marketplace model running is a four-step sequence, and each step is its own tool
so the caller can inspect the result before continuing:

    marketplace_search / marketplace_get_dpk   find a model package
    model_install                              install the DPK as an app in a project
    model_create                               create a model from one of the app's components
    model_deploy                               start a service that serves predictions
"""

from typing import Annotated, Any, Literal

import dtlpy as dl
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from .. import outputs, paging, serialize
from ..context import get_project, marketplace_dpks
from ..guards import confirm_or_elicit
from ..progress import report_progress
from ..schemas import PiiMarkerSpec
from .pii import apply_marker_filter, marker_exclusion_warning

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
MUTATING = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False
)
INSTALLATION_FIELDS = {"components", "compute", "settings"}
SERVICE_CONFIG_FIELDS = {"runtime", "autoscaler", "concurrency", "replicas", "resources"}


def validate_overrides(
    value: dict[str, Any] | None, allowed: set[str], label: str, confirm: bool
) -> dict[str, Any] | None:
    """Validate raw platform overrides behind an explicit confirmation boundary."""
    if value is None:
        return None
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(
            f"Unsupported {label} fields: {', '.join(unknown)}. Allowed fields: {', '.join(sorted(allowed))}."
        )
    if not confirm:
        raise ValueError(f"Refusing {label}: set confirm=true after reviewing the platform overrides.")
    return dict(value)


def component_output_type(app: Any, dpk_model_name: str) -> str | None:
    """The ``outputType`` a DPK's model component declares, if it declares one.

    ``dtlpy``'s ``models.create`` reads ``moduleName``, ``configuration``, ``tags``,
    ``description`` and ``status`` off the component but **not** ``outputType``: it defaults to
    ``AnnotationType.CLASSIFICATION`` no matter what the component says (verified against
    1.125.6). CLIP declares ``embedding``, so leaving the default in place registers an embedding
    model as a classification model. The declared value is read here and passed explicitly.
    """
    components = getattr(getattr(app, "dpk", None), "components", None)
    for component in getattr(components, "models", None) or []:
        if component.get("name") == dpk_model_name:
            return component.get("outputType")
    return None


def model_ids_from_services(project: Any) -> list[str]:
    """Model ids the project's services prove exist, for corroborating an empty models list.

    A models list filtered by permission comes back 200 with zero items, which reads exactly like
    a project that has no models — observed with an API key whose token carried ``roles: []``,
    while a deployed CLIP model was running the whole time. A deployed model always leaves a
    service carrying ``metadata.ml.modelId``, and services are readable under project membership
    alone, so they are the second opinion.
    """
    try:
        services = paging.page_entities(project.services.list())
    except Exception:
        # A listing that cannot corroborate must not turn a legitimately empty result into one.
        return []
    found = {
        model_id
        for service in services
        if (model_id := ((getattr(service, "metadata", None) or {}).get("ml") or {}).get("modelId"))
    }
    return sorted(found)


def register(mcp: MCPServer) -> None:
    @mcp.tool(title="Install marketplace package", annotations=MUTATING)
    def model_install(
        project_id: Annotated[str, Field(description="Project to install the package into.")],
        dpk_id: Annotated[str | None, Field(description="Id of the DPK. Provide this or dpk_name.")] = None,
        dpk_name: Annotated[str | None, Field(description="Name of the DPK to install.")] = None,
        dpk_version: Annotated[str | None, Field(description="Version to install. Defaults to latest.")] = (
            None
        ),
        app_name: Annotated[str | None, Field(description="Name for the installed app.")] = None,
        custom_installation: Annotated[
            dict[str, Any] | None,
            Field(description="Overrides: components, compute, settings."),
        ] = None,
        confirm: Annotated[
            bool,
            Field(description="Required with custom_installation."),
        ] = False,
        wait: Annotated[
            bool,
            Field(
                description=(
                    "Wait for installation to finish. Installation runs as a platform "
                    "command and can take a while; set false to return immediately."
                )
            ),
        ] = True,
    ) -> outputs.InstalledAppSummary:
        """Install a marketplace package (DPK) as an app in a project.

        Installing does not create a model. Follow with model_create, using one of the
        model component names from marketplace_get_dpk.
        """
        if not dpk_id and not dpk_name:
            raise ValueError("Provide either dpk_id or dpk_name.")
        project = get_project(project_id)
        dpk = marketplace_dpks().get(dpk_id=dpk_id, dpk_name=dpk_name, dpk_version=dpk_version)
        app = project.apps.install(
            dpk=dpk,
            app_name=app_name,
            custom_installation=validate_overrides(
                custom_installation, INSTALLATION_FIELDS, "custom_installation", confirm
            ),
            wait=wait,
        )
        result = serialize.app(app)
        components = getattr(dpk, "components", None)
        result["available_model_components"] = [
            component.get("name") for component in (getattr(components, "models", None) or [])
        ]
        return result

    @mcp.tool(title="List installed apps", annotations=READ_ONLY)
    def app_list(
        project_id: Annotated[str, Field(description="Project whose installed apps to list.")],
        page: Annotated[int, Field(ge=0, description="Zero-based page number.")] = 0,
        page_size: Annotated[int, Field(ge=1, le=paging.MAX_LIMIT, description="Apps per page.")] = (
            paging.DEFAULT_LIMIT
        ),
        limit: Annotated[int, Field(ge=1, le=paging.MAX_LIMIT, description="Max apps to return.")] = (
            paging.DEFAULT_LIMIT
        ),
    ) -> paging.ListResult:
        """List apps installed in a project, i.e. what has been installed from the marketplace."""
        project = get_project(project_id)
        filters = dl.Filters(resource=dl.FiltersResource.APP)
        filters.page = page
        filters.page_size = page_size
        return paging.from_paged(project.apps.list(filters=filters), serialize.app, limit=limit)

    @mcp.tool(title="Create model", annotations=MUTATING)
    def model_create(
        project_id: Annotated[str, Field(description="Project to create the model in.")],
        app_id: Annotated[str, Field(description="Installed app id, from model_install.")],
        dpk_model_name: Annotated[
            str,
            Field(description="Model component name in the app's DPK, from marketplace_get_dpk."),
        ],
        model_name: Annotated[str, Field(min_length=1, description="Name for the new model.")],
        dataset_id: Annotated[
            str | None, Field(description="Dataset to bind the model to; required for training.")
        ] = None,
        labels: Annotated[
            list[str] | None,
            Field(description="Labels the model predicts. Defaults to the component's."),
        ] = None,
        ontology_id: Annotated[
            str | None, Field(description="Take labels from this ontology instead.")
        ] = None,
        description: Annotated[str | None, Field(description="Optional description.")] = None,
        configuration: Annotated[
            dict[str, Any] | None,
            Field(description="Configuration overrides. Defaults to the component's. Requires confirm=true."),
        ] = None,
        output_type: Annotated[
            str | None, Field(description="e.g. 'embedding'. Defaults to the component's.")
        ] = None,
        confirm: Annotated[
            bool,
            Field(description="Required when configuration overrides are provided."),
        ] = False,
        ctx: Context | None = None,
    ) -> outputs.ModelSummary:
        """Create a model from a component of an installed app.

        A model must come from an installed app: install the package first with
        model_install. dpk_model_name must match one of that package's model components.

        `output_type` defaults to the component's, which `dtlpy` ignores — it would register even
        an embedding model like CLIP as a classification model.
        """
        if configuration is not None:
            confirm_or_elicit(
                ctx,
                confirm,
                "create this model with configuration overrides",
                "the overrides are passed to the platform as given and their keys are "
                "model-adapter-specific, so this server cannot validate them",
            )
        project = get_project(project_id)
        app = project.apps.get(app_id=app_id)
        resolved_output_type = output_type or component_output_type(app, dpk_model_name)
        extra = {"output_type": resolved_output_type} if resolved_output_type else {}
        model = project.models.create(
            model_name=model_name,
            app=app,
            dpk_model_name=dpk_model_name,
            dataset_id=dataset_id,
            labels=labels,
            ontology_id=ontology_id,
            description=description,
            configuration=dict(configuration) if configuration is not None else None,
            project_id=project_id,
            **extra,
        )
        return serialize.model(model)

    @mcp.tool(title="Deploy model", annotations=MUTATING)
    def model_deploy(
        project_id: Annotated[str, Field(description="Project that owns the model.")],
        model_id: Annotated[str, Field(description="Id of the model to deploy.")],
        service_config: Annotated[
            dict[str, Any] | None,
            Field(
                description=(
                    "Supported service overrides: runtime, autoscaler, concurrency, replicas and resources."
                )
            ),
        ] = None,
        confirm: Annotated[
            bool,
            Field(description="Must be true: deploying provisions billable compute."),
        ] = False,
        ctx: Context | None = None,
    ) -> outputs.ModelDeployResult:
        """Deploy a model as a running service that serves predictions.

        This provisions compute and can take a few minutes.
        """
        confirm_or_elicit(
            ctx,
            confirm,
            "deploy this model",
            "it provisions billable compute that keeps running until the service is stopped",
        )
        project = get_project(project_id)
        report_progress(ctx, 0, 100, f"Deploying model {model_id}...")
        service = project.models.deploy(
            model_id=model_id,
            # Reaching here means the call was already approved (confirm=true, or elicitation),
            # so service_config's own shape check is not re-gated on the raw confirm flag.
            service_config=validate_overrides(service_config, SERVICE_CONFIG_FIELDS, "service_config", True),
        )
        report_progress(ctx, 100, 100, "Model deployed.")
        return {"model_id": model_id, "service": serialize.service(service)}

    @mcp.tool(title="Train model", annotations=MUTATING)
    def model_train(
        project_id: Annotated[str, Field(description="Project that owns the model.")],
        model_id: Annotated[str, Field(description="Id of the model to train.")],
        service_config: Annotated[
            dict[str, Any] | None,
            Field(
                description=(
                    "Supported service overrides: runtime, autoscaler, concurrency, replicas and resources."
                )
            ),
        ] = None,
        confirm: Annotated[
            bool,
            Field(description="Must be true: training runs are billable."),
        ] = False,
        ctx: Context | None = None,
    ) -> outputs.ModelSummary:
        """Start a training run for a model.

        The model needs a dataset and train/validation subsets configured. Training runs
        asynchronously on the platform.
        """
        confirm_or_elicit(
            ctx, confirm, "start a training run", "training consumes billable compute on the platform"
        )
        project = get_project(project_id)
        project.models.train(
            model_id=model_id,
            # See model_deploy: reaching here means the call was already approved.
            service_config=validate_overrides(service_config, SERVICE_CONFIG_FIELDS, "service_config", True),
        )
        return serialize.model(project.models.get(model_id=model_id))

    @mcp.tool(title="Run a deployed model over items", annotations=MUTATING)
    def model_predict(
        project_id: Annotated[str, Field(description="Project that owns the model.")],
        model_id: Annotated[
            str,
            Field(description="An installed, deployed model; model_list shows status."),
        ],
        item_ids: Annotated[
            list[str] | None,
            Field(description="Items to run over. Provide this or dataset_id, not both."),
        ] = None,
        dataset_id: Annotated[
            str | None,
            Field(description="Run over an entire dataset. Cost scales with its item count."),
        ] = None,
        confirm: Annotated[
            bool,
            Field(description="Must be true: inference is billable and writes annotations."),
        ] = False,
        ctx: Context | None = None,
    ) -> outputs.ModelPredictionResult:
        """Run an installed model over items so its predictions land as annotations.

        Use this to learn what is actually *in* a dataset when filenames and labels do not say.

        Two consequences to state before confirming. Inference is **billable**, per item over a
        whole dataset. And predictions are **written onto the items as annotations**, so a dataset
        someone only wanted to explore comes back changed; `annotation_delete_by_model` undoes
        exactly this model's contribution.
        """
        if bool(item_ids) == bool(dataset_id):
            raise ValueError("Provide exactly one of item_ids or dataset_id.")
        scope = f"{len(item_ids)} item(s)" if item_ids else f"every item in dataset {dataset_id}"
        confirm_or_elicit(
            ctx,
            confirm,
            f"run model {model_id} over {scope}",
            "inference is billable and its predictions are written onto the items as annotations",
        )
        project = get_project(project_id)
        model = project.models.get(model_id=model_id)
        status = getattr(model, "status", None)
        execution = model.predict(item_ids=item_ids, dataset_id=dataset_id)
        return {
            "model_id": model_id,
            "model_status": status,
            "scope": scope,
            "execution_id": getattr(execution, "id", None),
            "note": (
                "Prediction runs asynchronously on the platform. Annotations appear on the items "
                "as it progresses — poll annotation_list rather than assuming the work is done "
                "when this returns. If the model is not deployed, the run will not produce "
                "annotations; check model_status."
            ),
        }

    @mcp.tool(title="Define a training or validation subset", annotations=MUTATING)
    def model_set_subset(
        project_id: Annotated[str, Field(description="Project that owns the model.")],
        model_id: Annotated[str, Field(description="Model whose subset to define.")],
        subset: Annotated[
            Literal["train", "validation"], Field(description="Which subset this filter defines.")
        ],
        metadata_field: Annotated[
            str | None,
            Field(description="Only items whose user metadata carries this field, e.g. 'reviewed'."),
        ] = None,
        metadata_value: Annotated[
            Any | None, Field(description="Required value for metadata_field. Defaults to true.")
        ] = None,
        annotated_only: Annotated[
            bool | None, Field(description="Restrict to items that carry annotations.")
        ] = None,
        directory: Annotated[str | None, Field(description="Restrict to items under this folder.")] = None,
        exclude_pii: Annotated[
            PiiMarkerSpec | None,
            Field(description="Exclude items a PII scan flagged. Same marker shape as pii_report."),
        ] = None,
    ) -> outputs.ModelSubsetResult:
        """Define which of a model's dataset items it trains or validates on.

        Without this, training draws on whatever the dataset holds — including items whose
        annotations nobody has reviewed, and items a PII scan flagged. Filtering on a review
        marker written through `item_update` is what makes a review step actually bind;
        `exclude_pii` is what keeps flagged data out of a training run.
        """
        if metadata_field is None and annotated_only is None and directory is None and exclude_pii is None:
            raise ValueError(
                "Provide at least one of metadata_field, annotated_only, directory or exclude_pii: "
                "an unfiltered subset is the same as no subset."
            )
        project = get_project(project_id)
        model = project.models.get(model_id=model_id)

        filters = dl.Filters(resource=dl.FiltersResource.ITEM)
        if metadata_field is not None:
            value = True if metadata_value is None else metadata_value
            filters.add(field=f"metadata.user.{metadata_field}", values=value)
        if annotated_only is not None:
            filters.add(field="annotated", values=annotated_only)
        if directory is not None:
            filters.add(field="dir", values=directory)
        pii_warning = None
        pii_filter = None
        if exclude_pii is not None:
            apply_marker_filter(filters, exclude_pii, flagged=False)
            pii_warning = marker_exclusion_warning(exclude_pii)
            pii_filter = filters.prepare()

        # Count before applying, not after: if the count read fails, the tool raises and the
        # caller must be able to trust that nothing was applied. Applying first would leave the
        # subset live on a model the caller was told had failed.
        # A subset matching nothing fails at training time, hours later; say so now.
        dataset = project.datasets.get(dataset_id=model.dataset_id)
        matched = dataset.items.list(filters=filters)
        matched_count = getattr(matched, "items_count", None)
        model.add_subset(subset_name=subset, subset_filter=filters)
        warnings = [
            message
            for message in (
                (
                    f"The {subset} subset currently matches no items — training would have nothing "
                    "to draw on. Check the filter before training."
                    if matched_count == 0
                    else None
                ),
                pii_warning,
            )
            if message
        ]
        return {
            "model_id": model_id,
            "subset": subset,
            "matched_items": matched_count,
            "pii_excluded": exclude_pii is not None,
            "pii_exclusion_filter": pii_filter,
            "warning": " ".join(warnings) or None,
        }

    @mcp.tool(title="Remove a subset definition", annotations=MUTATING)
    def model_delete_subset(
        project_id: Annotated[str, Field(description="Project that owns the model.")],
        model_id: Annotated[str, Field(description="Model whose subset to remove.")],
        subset: Annotated[
            Literal["train", "validation"], Field(description="Which subset definition to remove.")
        ],
    ) -> outputs.ModelSubsetRemovalResult:
        """Remove a training or validation subset definition from a model."""
        project = get_project(project_id)
        model = project.models.get(model_id=model_id)
        model.delete_subset(subset_name=subset)
        return {"model_id": model_id, "subset": subset, "removed": True}

    @mcp.tool(title="List models", annotations=READ_ONLY)
    def model_list(
        project_id: Annotated[str, Field(description="Project to list models in.")],
        page: Annotated[int, Field(ge=0, description="Zero-based page number.")] = 0,
        page_size: Annotated[int, Field(ge=1, le=paging.MAX_LIMIT, description="Models per page.")] = (
            paging.DEFAULT_LIMIT
        ),
        limit: Annotated[int, Field(ge=1, le=paging.MAX_LIMIT, description="Max models to return.")] = (
            paging.DEFAULT_LIMIT
        ),
        fields: Annotated[list[str] | None, Field(description=paging.FIELDS_DESCRIPTION)] = None,
    ) -> paging.ListResult:
        """List models in a project.

        An empty first page is corroborated against the project's services before being reported
        as "no models": an unauthorized models query returns an empty list, not a 403.
        """
        project = get_project(project_id)
        filters = dl.Filters(resource=dl.FiltersResource.MODEL)
        filters.page = page
        filters.page_size = page_size
        result = paging.from_paged(
            project.models.list(filters=filters), serialize.model, limit=limit, fields=fields
        )
        if page == 0 and not result["items"] and (hidden := model_ids_from_services(project)):
            raise RuntimeError(
                f"The models list came back empty, but {len(hidden)} deployed model(s) exist in "
                f"this project — {', '.join(hidden)} — each with a running service. The list is "
                "being filtered, most likely because these credentials lack ML permissions "
                "(an API key with no roles gets an empty list, not a 403). Treat this project as "
                "'models unknown', not 'no models', and check the key's permissions."
            )
        return result

    @mcp.tool(title="Get model", annotations=READ_ONLY)
    def model_get(
        project_id: Annotated[str, Field(description="Project that owns the model.")],
        model_id: Annotated[str, Field(description="Id of the model.")],
    ) -> outputs.ModelSummary:
        """Fetch one model by id."""
        project = get_project(project_id)
        return serialize.model(project.models.get(model_id=model_id))

    @mcp.tool(title="Delete model", annotations=DESTRUCTIVE)
    def model_delete(
        project_id: Annotated[str, Field(description="Project that owns the model.")],
        model_id: Annotated[str, Field(description="Id of the model to delete.")],
        confirm: Annotated[
            bool, Field(description="Must be true to proceed. Ask the user before deleting a model.")
        ] = False,
        ctx: Context | None = None,
    ) -> outputs.DeleteResult:
        """Delete a model. Does not uninstall the app it came from."""
        confirm_or_elicit(ctx, confirm, "delete this model", "it cannot be undone")
        project = get_project(project_id)
        model = project.models.get(model_id=model_id)
        name = model.name
        project.models.delete(model=model)
        return {"deleted": True, "id": model_id, "name": name}

    @mcp.tool(title="Uninstall app", annotations=DESTRUCTIVE)
    def app_uninstall(
        project_id: Annotated[str, Field(description="Project that owns the app.")],
        app_id: Annotated[str, Field(description="Id of the installed app to remove.")],
        confirm: Annotated[
            bool, Field(description="Must be true to proceed. Ask the user before uninstalling.")
        ] = False,
        ctx: Context | None = None,
    ) -> outputs.AppUninstallResult:
        """Uninstall an app from a project.

        Models created from the app stop working, so delete or migrate them first.
        """
        confirm_or_elicit(ctx, confirm, "uninstall this app", "models created from it will stop working")
        project = get_project(project_id)
        ok = project.apps.uninstall(app_id=app_id)
        return {"uninstalled": bool(ok), "id": app_id}
