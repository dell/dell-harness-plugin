---
name: detect-and-handle-dataloop-pii
description: >
  Assess and handle personally identifiable information in authorized Dataloop
  datasets using aggregate reports, reviewed findings, metadata writeback, and
  filters. Use for PII inventory, remediation planning, or policy-driven
  exclusion. Never reveal detected raw PII values or certify a dataset as
  PII-free.
---

# Detect and Handle Dataloop PII

Use the `models` profile, adding `discovery` when project and dataset resolution
is needed. Treat all inputs and findings as sensitive.

## Assess without exposing values

1. Resolve the authorized project and dataset by ID.
2. Ask for the intended scope, PII categories, and applicable handling policy
   when they are not explicit.
3. Run `pii_report` first. Report categories, counts, coverage, filters, and
   limitations; do not reproduce raw detected strings.
4. Use a bounded `item_list` or `item_get` only when the user has a legitimate
   need to investigate specific records.

A no-findings report is not proof that a dataset is PII-free. State detector,
coverage, and sampling limitations.

## Review the handling plan

Separate the possible changes:

- `pii_set_findings` records reviewed finding metadata.
- `pii_writeback_code` produces the reviewed Dataloop code path used for
  writeback.
- `pii_filter` applies an approved policy filter.

Before any write, show the exact project, dataset, filter, PII categories,
metadata fields, record count when available, and rollback or correction path.
Obtain explicit approval. Do not store raw PII values in user metadata.
Generated code requires review and the server's code confirmation.

## Verify the outcome

Read back aggregate findings after the change. Report affected counts and policy
state, not sensitive values. If the actual count differs materially from the
preview, stop and ask the user before continuing.

## Preserve boundaries

Do not change identity, infer access to hidden datasets, or weaken Dataloop
permissions. Do not delete source records merely because PII was detected. A
request to remove annotations or data is a separate destructive operation and
requires a dry run, blast-radius review, and explicit confirmation.

## Format the answer

Report scope, detector or rule, categories and counts, reviewed action, approval
status, verification evidence, and limitations. Use redacted examples only when
needed to explain a category.
