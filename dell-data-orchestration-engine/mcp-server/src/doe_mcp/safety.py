# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The MCP safety labels every tool is declared with.

One shared set instead of per-module copies, so a tool's label says which rule it falls under.
`tests/test_tool_safety_labels.py` pins the label of every advertised tool.

* destructive: deletes something, or overwrites existing state in place.
* idempotent: repeating the call with the same arguments has no further effect.
* open world: reads or installs third-party marketplace content.
"""

from mcp.types import ToolAnnotations

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
#: Reads the shared marketplace catalogue, whose packages are published by third parties.
READ_ONLY_MARKETPLACE = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)
#: Adds something new each call: create, upload, add, run, bill.
ADDITIVE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)
#: Installs third-party marketplace content into the project.
ADDITIVE_MARKETPLACE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True
)
#: Changes state without losing any, and repeating it changes nothing further.
IDEMPOTENT_WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
#: Replaces existing state in place; the previous value is gone.
OVERWRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False
)
#: Deletes or stops an exact target; repeating it finds nothing more to remove.
DELETE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False
)
