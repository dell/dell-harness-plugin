"""Structural validation for raw DQL filter dicts.

``dtlpy`` passes ``custom_filter`` straight through to the platform — see
``dtlpy.entities.filters.Filters.prepare``/``__generate_custom_query`` — so a malformed
filter is a defect that only surfaces after a real platform call (``pipeline_install``, or
``load_existing_data`` firing). This module catches that class of mistake before any network
call, matching the discriminated-union node specs' "reject before any platform call"
property in ``schemas.py``.

**This validator does not police operator names, deliberately.** An earlier version rejected
anything outside ``dtlpy``'s ``FiltersOperations`` enum, which was wrong twice over: that enum
is the set ``Filters.add()`` can *emit*, and ``custom_filter`` bypasses ``add()`` entirely, so
raw DQL is bound by the platform's grammar instead. The platform's grammar is not enumerable
from anything available here — DOE's own ``/docs/dql`` and ``/docs/dql-operators`` pages
disagree with each other and with the SDK (``$ieq`` appears on one page and in neither of the
others), and no live verification of the true operator matrix exists. The result was a
validator that rejected ``$ieq`` and Mongo-style embedded-document equality while waving
through ``{"$in": "not-a-list"}``.

Since the caller is an LLM, a false positive is strictly worse than a false negative: told
"that is not a DQL operator", a model believes it and rewrites a correct query into a wrong
one. A false negative costs one platform round-trip. So an unrecognised ``$operator`` passes
through, and what is enforced is only what is provably wrong regardless of dialect: shapes
that cannot mean anything (an empty operator object, a logical operator without a list of
clauses, ``$in`` without a list, ``$exists`` without a boolean) and a depth cap.

``KNOWN_OPERATORS`` therefore exists to make error messages helpful, not to gate anything.

Validation here is structural, not semantic: it does not know whether a field path exists on
any given dataset's items, only whether the query shape can mean anything at all.
"""

from typing import Any

#: Operators we know of, used only to make messages more helpful — never to reject.
#: Union of ``dtlpy.entities.filters.FiltersOperations``/``FiltersMethod`` and the operators
#: DOE's DQL reference documents. Pinned as a *superset* of dtlpy's enum by
#: ``test_known_operators_is_a_superset_of_dtlpys_own_enum``.
KNOWN_OPERATORS = frozenset(
    {
        "$and",
        "$or",
        "$eq",
        "$ieq",  # documented at docs.dataloop.ai/docs/dql; absent from dtlpy's enum
        "$ne",
        "$gt",
        "$gte",
        "$lt",
        "$lte",
        "$in",
        "$nin",
        "$exists",
        "$match",
    }
)

#: Operators that combine nested filter expressions and so take a list of clauses.
LOGICAL_OPERATORS = frozenset({"$and", "$or"})

#: Operators whose value must be a list.
LIST_OPERATORS = frozenset({"$in", "$nin"})

#: Deepest nesting accepted before bailing out. Guards against a pathologically nested filter
#: raising ``RecursionError`` — an opaque internal failure — instead of the actionable
#: ``ValueError`` this module's contract promises. Far beyond any hand-written query.
MAX_DEPTH = 32


def _validate_operator_map(value: dict[str, Any], path: str) -> None:
    """Check a ``{field: {...}}`` value that is being used as an operator map."""
    if not value:
        raise ValueError(f"{path} is an empty operator object, which matches nothing. Remove it.")
    literal_keys = sorted(key for key in value if not key.startswith("$"))
    if literal_keys:
        raise ValueError(
            f"{path} mixes operators with literal keys {literal_keys}. Use either an operator "
            f'object like {{"$eq": value}} or a literal document to match, not both.'
        )
    for operator, operand in value.items():
        if operator in LIST_OPERATORS and not isinstance(operand, list):
            raise ValueError(
                f"{path}.{operator} needs a list of values, got {type(operand).__name__}. "
                f'Write {{"{operator}": [value]}} even for a single value.'
            )
        if operator == "$exists" and not isinstance(operand, bool):
            raise ValueError(f"{path}.$exists needs true or false, got {type(operand).__name__}.")


def validate_filter_body(body: Any, *, path: str = "filter", depth: int = 0) -> None:
    """Raise ``ValueError`` if ``body`` cannot be a valid DQL filter expression.

    Accepts the shorthand equality form (``{"field": value}``), the explicit operator form
    (``{"field": {"$eq": value}}``), and embedded-document equality (``{"field": {"key":
    value}}``) — dtlpy and DOE's own examples use all three. An empty body is valid and
    matches everything.

    Unrecognised ``$operators`` are passed through rather than rejected; see the module
    docstring for why.
    """
    if depth > MAX_DEPTH:
        raise ValueError(
            f"{path} is nested more than {MAX_DEPTH} levels deep. That is far past anything DQL "
            "needs; check for a filter that accidentally wraps itself."
        )
    if not isinstance(body, dict):
        raise ValueError(f"{path} must be a JSON object, got {type(body).__name__}.")

    for key, value in body.items():
        if not key.startswith("$"):
            # A field clause. A dict value is an operator map when any key is an operator, and a
            # literal embedded document otherwise ({"metadata.system.refs": {"id": "abc"}}). An
            # empty dict is neither and matches nothing, so route it here to be rejected.
            if isinstance(value, dict) and (not value or any(k.startswith("$") for k in value)):
                _validate_operator_map(value, f"{path}.{key}")
            continue
        if key in LOGICAL_OPERATORS:
            if not isinstance(value, list) or not value:
                raise ValueError(f"{path}.{key} must be a non-empty list of filter expressions.")
            for index, clause in enumerate(value):
                validate_filter_body(clause, path=f"{path}.{key}[{index}]", depth=depth + 1)
        elif isinstance(value, list):
            # An operator we do not know, shaped like a logical one ($nor, $not, ...). Do not
            # judge the name, but its clauses still have to be well-formed expressions.
            for index, clause in enumerate(value):
                validate_filter_body(clause, path=f"{path}.{key}[{index}]", depth=depth + 1)


def validate_data_filters(data_filters: Any) -> None:
    """Validate a ``data_filters`` payload as passed to ``DatasetNodeSpec``.

    Mirrors ``Filters.__generate_custom_query``: a top-level ``filter`` key holds the
    expression; if absent, the whole dict *is* the expression. ``resource``, ``page``,
    ``pageSize``, ``sort`` and ``join`` are legal siblings of ``filter`` and are left alone,
    except that ``join``'s own nested ``filter`` is validated too — it is an ordinary filter
    expression and gets no scrutiny otherwise.
    """
    if not isinstance(data_filters, dict):
        raise ValueError(f"data_filters must be a JSON object, got {type(data_filters).__name__}.")
    if "filter" in data_filters:
        validate_filter_body(data_filters["filter"])
        join = data_filters.get("join")
        if isinstance(join, dict) and "filter" in join:
            validate_filter_body(join["filter"], path="join.filter")
        return
    validate_filter_body(data_filters)
