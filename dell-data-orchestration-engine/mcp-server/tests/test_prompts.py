"""MCP Prompts wrapping the three docs/guides/*.md workflows' opening prompts (plan §4 Phase 2)."""

import asyncio

from mcp import Client

from doe_mcp.server import build_server


def run(coro):
    return asyncio.run(coro)


def test_all_three_workflow_prompts_are_advertised():
    async def list_prompts():
        async with Client(build_server()) as client:
            return await client.list_prompts()

    names = {prompt.name for prompt in run(list_prompts()).prompts}

    assert names == {"explore_dataset", "find_and_handle_pii", "build_rag_pipeline"}


def test_each_prompt_returns_nonempty_message_text():
    async def get_all():
        async with Client(build_server()) as client:
            return {
                name: await client.get_prompt(name)
                for name in ("explore_dataset", "find_and_handle_pii", "build_rag_pipeline")
            }

    results = run(get_all())

    for name, result in results.items():
        assert result.messages, f"{name} returned no messages"
        text = result.messages[0].content.text
        assert len(text) > 100, f"{name}'s prompt text looks truncated"
