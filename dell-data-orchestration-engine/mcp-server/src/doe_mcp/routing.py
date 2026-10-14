# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Matching a plain-English request to the tool or skill that serves it.

**The architectural point.** A driving model normally makes ~18 separate tool choices to complete a
workflow, each one stochastic. Routing collapses that into **one** decision, which can then be
gated by a human, measured by the eval harness, and — because it is a single call with a single
input — *cached*. Everything downstream is already deterministic (`doe_mcp.blueprint`). So the
shape is: one stochastic step, gated and measured, then deterministic execution.

That is a narrowing of non-determinism, not its elimination, and this module does not claim
otherwise. What the cache does give is genuine repeatability at the routing layer: the same request
string returns the same route for the life of the process, so a re-run does not silently take a
different path.

**Pluggable, and deliberately not a model.** `Router` is a `Protocol`. The default implementation
is lexical — weighted bag-of-words cosine over each tool's advertised name, title and description —
because that is measurable today with no credentials and no provider choice, and because
`scripts/measure_intent_routing.py` shows it already reaches the right tool in the top 3 for three
quarters of a sample corpus. A better router (a frontier model, or a fine-tuned small model) drops
in behind the same protocol.

**If that better router is a model, it belongs on DOE.** This project's convention is to use
models deployed on the platform rather than downloading and running them here. The former local
CLIP exception was retired: text-query visual search now uses a model deployed on Dataloop.
A routing model should likewise be a DOE model called through `model.predict`, not weights
loaded into this process.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Protocol

_WORD = re.compile(r"[a-z][a-z0-9]+")

#: Words that appear across most tool descriptions and carry no routing signal. Dropping them stops
#: "project"/"dataset" — which qualify nearly every tool here — from dominating the similarity.
STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "this",
        "that",
        "from",
        "into",
        "not",
        "project",
        "dataset",
        "item",
        "items",
        "tool",
        "call",
        "use",
        "when",
        "what",
        "which",
        "than",
        "its",
        "are",
        "was",
        "you",
        "your",
        "can",
        "will",
    }
)


def tokens(text: str) -> list[str]:
    return [word for word in _WORD.findall(text.lower()) if word not in STOPWORDS]


@dataclass(frozen=True)
class Candidate:
    """One possible destination for a request."""

    name: str
    score: float


@dataclass(frozen=True)
class Route:
    """A proposed destination, plus the runners-up so a caller can judge the call."""

    intent: str
    candidates: tuple[Candidate, ...]
    router: str

    @property
    def best(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

    @property
    def confident(self) -> bool:
        """Whether the top candidate stands clear of the next one.

        A margin, not an absolute score: cosine values are not calibrated across queries, but "the
        winner beat the runner-up by half again" travels. An unconfident route is the signal to ask
        rather than assume — which is exactly the human gate this architecture depends on.
        """
        if not self.candidates:
            return False
        if len(self.candidates) == 1:
            return self.candidates[0].score > 0
        top, second = self.candidates[0].score, self.candidates[1].score
        return top > 0 and top >= second * 1.5


class Router(Protocol):
    """Maps a request to ranked candidates, given the advertised catalogue."""

    name: str

    def route(self, intent: str, catalogue: dict[str, str], limit: int) -> Route: ...


class LexicalRouter:
    """Weighted bag-of-words cosine. No model, no network, fully deterministic.

    Deterministic in the strong sense — same intent and same catalogue give the same ranking, with
    no sampling anywhere — which makes it the honest baseline to measure a model against rather
    than a placeholder.
    """

    name = "lexical"

    def route(self, intent: str, catalogue: dict[str, str], limit: int = 5) -> Route:
        documents = {name: Counter(tokens(text)) for name, text in catalogue.items()}
        total = len(documents) or 1
        frequency = Counter(word for counts in documents.values() for word in counts)
        idf = {word: math.log(total / (1 + count)) + 1.0 for word, count in frequency.items()}

        query = Counter(tokens(intent))
        q_vec = {word: count * idf.get(word, 0.0) for word, count in query.items()}
        q_norm = math.sqrt(sum(value * value for value in q_vec.values())) or 1.0

        scored: list[Candidate] = []
        for name, counts in documents.items():
            d_vec = {word: count * idf.get(word, 0.0) for word, count in counts.items()}
            d_norm = math.sqrt(sum(value * value for value in d_vec.values())) or 1.0
            dot = sum(value * d_vec.get(word, 0.0) for word, value in q_vec.items())
            scored.append(Candidate(name, dot / (q_norm * d_norm)))

        scored.sort(key=lambda candidate: (-candidate.score, candidate.name))
        return Route(intent=intent, candidates=tuple(scored[:limit]), router=self.name)


@dataclass
class CachingRouter:
    """Memoises routes, so the same request takes the same path every time it is asked.

    The one place in this architecture where determinism is genuinely *added* rather than
    preserved: whatever the inner router does — including a sampled model — the second identical
    request returns the first answer. Keyed on the exact request string and the catalogue's shape,
    so a changed tool surface invalidates rather than silently reusing a stale route.
    """

    inner: Router
    _cache: dict[tuple[str, int, str], Route] = field(default_factory=dict, repr=False)

    @property
    def name(self) -> str:
        return f"cached:{self.inner.name}"

    def route(self, intent: str, catalogue: dict[str, str], limit: int = 5) -> Route:
        key = (intent, limit, ",".join(sorted(catalogue)))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        result = self.inner.route(intent, catalogue, limit)
        self._cache[key] = result
        return result

    @property
    def hits(self) -> int:
        return len(self._cache)


def catalogue_from(tools: Any) -> dict[str, str]:
    """The routing text for each tool: the same name, title and description a model would see."""
    return {
        tool.name: f"{tool.name.replace('_', ' ')} {getattr(tool, 'title', '') or ''} "
        f"{tool.description or ''}"
        for tool in tools
    }
