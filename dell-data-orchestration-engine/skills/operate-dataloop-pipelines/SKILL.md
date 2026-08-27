---
name: operate-dataloop-pipelines
description: >
  Validate, install, run, inspect, and recover authorized Dell Data Orchestration
  Engine pipelines through Dataloop. Use when a user asks to validate a pipeline,
  start an approved execution, inspect execution status, or terminate an exact
  stuck execution. Do not use to design a RAG graph, explore datasets, or manage
  local operating-system processes.
---

# Operate Dataloop Pipelines

Use stable platform IDs and keep every action inside the authorized project.
Treat validation and status inspection as read-only. Installation, execution,
and termination require an explicit user-visible plan and approval.

## Resolve and validate

1. Resolve the project with `project_list` and `project_get` when the project ID
   was not supplied. Never guess an ID from a display name.
2. Resolve the exact pipeline with `pipeline_list` and `pipeline_get`.
3. Call `pipeline_validate` before installation or execution. Report validation
   errors and stop; do not work around them by changing unrelated resources.
4. If installation is required, show the project ID, pipeline ID, intended
   effect, and likely duration. Ask for approval, then call `pipeline_install`.

## Execute an approved pipeline

1. State the exact project and pipeline IDs plus any input scope.
2. Ask for explicit approval immediately before `pipeline_execute`.
3. Call `pipeline_execute` once. Do not retry an ambiguous timeout because the
   first request may have started a run.
4. Capture the returned execution ID and inspect it with `execution_list`.
5. Report the observed status, identifiers, and any platform error separately
   from planned next steps.

## Recover a stuck execution

1. Use `execution_list` to find the exact execution in the authorized project.
2. Confirm that the execution is still active and record its pipeline and
   execution IDs. Explain that termination can leave partial writes or outputs.
3. Ask for explicit approval immediately before `execution_terminate`.
4. Terminate only the confirmed execution ID. Never terminate every matching
   run or infer the target from an ambiguous name.
5. Read the status again and report what the platform returned.

## Safety boundaries

Do not install, execute, or terminate when the project or pipeline is ambiguous.
Do not switch identities, broaden permissions, expose credentials, or describe
a submitted request as successful without a platform result. For production
resources, prefer a dry run or disposable test pipeline when available.

## Response format

Return the project ID, pipeline ID, validation result, execution ID when one was
created, observed status, approved state changes, and any unresolved risk or
follow-up action.
