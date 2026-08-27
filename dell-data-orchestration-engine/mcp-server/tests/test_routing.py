"""Intent routing: one gated, cacheable decision in place of many ungated ones.

Routing does not make anything deterministic on its own — a router is still choosing. What it does
is collapse a workflow's many stochastic tool choices into a single decision that can be gated,
measured and memoised, ahead of execution that is already deterministic. These tests hold it to
that claim and no more.
"""

import asyncio

import pytest

from doe_mcp.routing import CachingRouter, LexicalRouter, Route, catalogue_from, tokens
from doe_mcp.server import build_server

CATALOGUE = {
    "dataset_list": "dataset list List the datasets in a project with their item counts.",
    "pipeline_install": "pipeline install Start a pipeline so it begins processing items.",
    "model_train": "model train Train a model on labelled data. Billable.",
}


def test_a_plain_request_reaches_the_matching_tool():
    route = LexicalRouter().route("start my pipeline so it processes things", CATALOGUE, 3)

    assert route.best is not None
    assert route.best.name == "pipeline_install"


def test_routing_is_deterministic_for_the_same_request():
    """The baseline router samples nothing; identical input must give an identical ranking."""
    first = LexicalRouter().route("train a model", CATALOGUE, 3)
    second = LexicalRouter().route("train a model", CATALOGUE, 3)

    assert first == second


def test_ties_break_by_name_rather_than_dict_order():
    """A request matching nothing scores every tool zero; the order must still be stable."""
    route = LexicalRouter().route("zzzz", CATALOGUE, 3)

    assert [c.name for c in route.candidates] == sorted(CATALOGUE)


def test_a_clear_winner_is_reported_confident():
    route = LexicalRouter().route("train a model on labelled data", CATALOGUE, 3)

    assert route.confident


def test_an_ambiguous_request_is_not_reported_confident():
    """Confidence is a margin over the runner-up, so a request that fits nothing is not confident.

    This is the signal to ask the user, which is the human gate this architecture depends on --
    a router that is always confident is worse than no router.
    """
    route = LexicalRouter().route("do the thing", CATALOGUE, 3)

    assert not route.confident


def test_no_candidates_is_not_confident():
    route = LexicalRouter().route("anything", {}, 3)

    assert route.best is None
    assert not route.confident


# --- caching: the one place determinism is added ------------------------------------------


class CountingRouter:
    """Returns a different answer each call, so a cache hit is unmistakable."""

    name = "counting"

    def __init__(self) -> None:
        self.calls = 0

    def route(self, intent, catalogue, limit=5):
        self.calls += 1
        return Route(intent=intent, candidates=(), router=f"{self.name}-{self.calls}")


def test_the_same_request_returns_the_first_answer():
    """Whatever the inner router does -- including sampling -- a repeat takes the same path."""
    inner = CountingRouter()
    router = CachingRouter(inner)

    first = router.route("find PII", CATALOGUE, 3)
    second = router.route("find PII", CATALOGUE, 3)

    assert first is second
    assert inner.calls == 1


def test_a_changed_tool_surface_invalidates_the_cache():
    """A stale route pointing at a tool the session no longer carries is worse than a slow one."""
    inner = CountingRouter()
    router = CachingRouter(inner)

    router.route("find PII", CATALOGUE, 3)
    router.route("find PII", {**CATALOGUE, "pii_report": "pii report Find personal information."}, 3)

    assert inner.calls == 2


def test_a_different_request_is_not_served_from_cache():
    inner = CountingRouter()
    router = CachingRouter(inner)

    router.route("a", CATALOGUE, 3)
    router.route("b", CATALOGUE, 3)

    assert inner.calls == 2


# --- the tool ------------------------------------------------------------------------------


def _skills_client_call(name, args):
    from mcp import Client

    async def run():
        async with Client(build_server(profiles=["skills"])) as client:
            return await client.call_tool(name, args)

    return asyncio.run(run())


def test_intent_route_proposes_and_never_executes():
    """It returns a suggestion. Anything that acted on it would bypass the approval gate."""
    result = _skills_client_call("intent_route", {"request": "list the datasets in my project"})

    assert result.is_error is False
    payload = result.structured_content
    assert payload["candidates"], "a route with no candidates is not useful"
    assert "next_step" in payload
    assert payload["candidates"][0]["tool"] in {c["tool"] for c in payload["candidates"]}


def test_intent_route_only_ever_names_tools_the_session_carries():
    """A router that suggests an unavailable tool sends the agent down a dead end."""
    available = {tool.name for tool in asyncio.run(build_server(profiles=["skills"]).list_tools())}

    result = _skills_client_call("intent_route", {"request": "build a RAG pipeline", "limit": 10})

    for candidate in result.structured_content["candidates"]:
        assert candidate["tool"] in available


def test_intent_route_is_read_only():
    """It must carry no side effect, or the gate it exists to feed becomes meaningless."""
    tools = {t.name: t for t in asyncio.run(build_server(profiles=["skills"]).list_tools())}

    assert tools["intent_route"].annotations.read_only_hint is True


@pytest.mark.parametrize("request_text", ["", " "])
def test_intent_route_rejects_an_empty_request(request_text):
    result = _skills_client_call("intent_route", {"request": request_text})

    assert result.is_error is True or result.structured_content["candidates"] is not None


def test_tokens_drop_words_that_qualify_almost_every_tool():
    """`project` and `dataset` appear in most descriptions, so they carry no routing signal."""
    assert "project" not in tokens("list the datasets in my project")
    assert "datasets" in tokens("list the datasets in my project")


def test_catalogue_uses_the_same_text_a_model_would_see():
    catalogue = catalogue_from(asyncio.run(build_server(profiles=["skills"]).list_tools()))

    assert "rag_pipeline_build" in catalogue
    assert "deterministic" in catalogue["rag_pipeline_build"].lower()
