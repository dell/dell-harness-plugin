---
name: build-dataloop-image-classifiers
description: >
  Plan and operate governed image-classification workflows in Dell Data
  Orchestration Engine (Dataloop), including dataset readiness, marketplace
  model selection, subsets, training, deployment, prediction, and annotation
  review. Use when a user requests an image classifier or model-assisted image
  labeling in Dataloop.
---

# Build Dataloop Image Classifiers

Use `discovery,models` for this workflow. Training, deployment, and inference
run on Dataloop compute and may consume quota or create persistent resources.

## Establish the experiment

Resolve the authorized project and image dataset. Use `dataset_profile`,
`annotation_labels`, and bounded `annotation_list` calls to assess media types,
class distribution, annotation provenance, and obvious gaps. Ask for the target
labels, success metric, train/validation/test policy, compute budget, and output
annotation expectations when they are not supplied.

Do not use identity recognition or infer sensitive traits. Do not treat existing
model annotations as verified ground truth.

## Select and prepare the model

1. Use `marketplace_search` with a narrow image-classification query.
2. Inspect the candidate with `marketplace_get_dpk`; explain version, components,
   inputs, outputs, and known fit before installation.
3. Present the proposed app, model, subsets, compute, and persistent resources.
4. After approval, call `model_install`, verify with `app_list`, and call
   `model_create` using the returned component identifiers.
5. Define subsets with `model_set_subset`; verify that filters are disjoint and
   match the approved dataset policy.

Marketplace search may be locally filtered and scan-limited. A missing result is
not proof that no package exists.

## Train, deploy, and predict

Each phase needs a separate, current approval because it can spend compute or
write annotations:

- Preview settings and call `model_train` with the required confirmation.
- Inspect `model_get`; deploy with `model_deploy` only after approval.
- Run `model_predict` first on a small approved subset, then inspect returned
  annotations with `annotation_list`.

Do not automatically expand inference to the full dataset. Compare output with
the approved metric and human-reviewed sample before recommending scale-up.

## Clean up safely

List the target with `model_get` or `app_list` before deletion. Explain impact
on services, predictions, and dependent workflows. Use `model_delete_subset`,
`model_delete`, or `app_uninstall` only after exact-target confirmation.

## Preserve boundaries

Honor project, dataset, model, compute, and audit controls. Do not request admin
credentials or bypass failed permissions. Treat training images and annotations
as potentially sensitive.

## Format the answer

Report dataset readiness, selected package and version, approved settings,
created IDs, stage status, evaluation evidence, compute or persistence impact,
and the next decision requiring user approval.
