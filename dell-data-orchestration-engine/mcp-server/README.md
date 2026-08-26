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
