"""MCP Prompts and golden evaluation traces must share the same workflow text."""

import asyncio

import pytest
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


TRACE_PROMPTS = [
    ("DATASET_EXPLORATION", "EXPLORE_DATASET_PROMPT"),
    ("PII_DETECTION", "FIND_AND_HANDLE_PII_PROMPT"),
    ("AGENTIC_RAG", "BUILD_RAG_PIPELINE_PROMPT"),
]


@pytest.mark.parametrize(("trace_name", "constant"), TRACE_PROMPTS)
def test_golden_trace_and_mcp_prompt_say_the_same_thing(trace_name, constant):
    """The evaluator must score the same instruction the MCP prompt serves."""
    from doe_mcp import prompts as prompts_module
    from doe_mcp.eval import traces as traces_module

    trace = getattr(traces_module, trace_name)
    prompt = getattr(prompts_module, constant)
    assert trace.intent.strip() == prompt.strip()
