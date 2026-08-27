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

This bundled copy incorporates the maintained runtime changes through upstream
commit `0e075f2` from `doe-mcp`, combined with the plugin's typed output
contracts and focused profile.

The default plugin launcher exposes 36 tools. The update adds bounded pipeline
execution, execution inspection and termination, and pre-install validation. It
also incorporates graph-mutation retry handling, stricter confirmation for
billable model operations, DQL and code-node validation, and the upstream
offline evaluation harness.

The upstream `skills` profile remains experimental and is not exposed by the
plugin launcher. The plugin continues to use its evaluated fine-grained RAG
workflow until that prototype has separate plugin evidence.
