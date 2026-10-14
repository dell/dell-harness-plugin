# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Functions defined in a real file, so ``inspect.getsourcelines`` can read them.

``dl.CodeNode`` needs that, which is the whole reason ``doe_mcp.code_node`` exists. These
let a test compare our string-built payload against dtlpy's own output.
"""


def my_handler(item, context):
    """Docstring."""
    if item is not None:
        print("kept")
    return item


def passthrough(item):
    return item
