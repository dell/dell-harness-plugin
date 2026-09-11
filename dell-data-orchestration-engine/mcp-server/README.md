# Bundled Dell Data Orchestration Engine MCP server

This directory contains the Python MCP implementation shipped with the Dell
Data Orchestration Engine plugin. It uses the Dataloop `dtlpy` SDK and is
executed by `../scripts/dataloop_mcp_launcher.py`.

The implementation began from the internal `doe-mcp` prototype. The
released runtime, tests, dependency constraints, and plugin-focused tool
profile are maintained here so installing the plugin does not depend on that
repository.

Install the runtime dependencies and optional command-line entry point from a
repository checkout:

```bash
python -m pip install "./dell-data-orchestration-engine/mcp-server"
```

Run the offline validation suite with the development dependencies installed:

```bash
python -m pip install -e "./dell-data-orchestration-engine/mcp-server[dev]"
python -m pytest dell-data-orchestration-engine/mcp-server/tests
```

Live validation requires a scoped Dataloop API key and an approved disposable
project. Do not use customer production data for smoke testing.

## Integrated source baseline

The source repository reviewed for this release is
`https://git.example.com/doe-mcp.git`. This bundled copy ports the
applicable safety and correctness changes reviewed through upstream commit
`49f7d44`, combined with the plugin's typed output contracts and focused
profile. It is a maintained plugin fork, not a byte-for-byte mirror.

The default plugin launcher exposes 36 tools. Version 0.3 adds a hard
confirmation gate for pipeline installation and for model configuration
overrides, repairs SDK state and cookie permissions, preserves nested platform
errors, adds annotation pagination, and stabilizes derived node IDs across
unrelated blueprint edits. The opt-in full profiles also gain project-scoped
trigger pagination, honest embedding counts, and platform-side text-query
embedding for visual search.

The upstream composite `skills` profile remains experimental and is not
exposed by the plugin launcher. The plugin continues to use its evaluated
fine-grained RAG workflow until that prototype has separate plugin evidence.
