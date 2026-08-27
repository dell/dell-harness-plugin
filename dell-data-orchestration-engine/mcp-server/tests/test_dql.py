"""Structural validation of raw DQL filter dicts, ahead of any platform call.

The governing principle, and the reason this file is shaped the way it is: a **false positive here
is worse than no validation at all**. The caller is an LLM, so a confident "that is not a DQL
operator" about a query that is in fact valid gets believed, and the model rewrites a correct query
into a wrong one. A false negative merely costs one platform round-trip. So the "must NOT raise"
block below is the load-bearing half of this suite, not an afterthought.
"""

import dtlpy as dl
import pytest

from doe_mcp import dql

# --- accepted shapes ---------------------------------------------------------------------

ACCEPTED = {
    "shorthand equality": {"filename": "/incoming"},
    "explicit operator": {"metadata.system.mimetype": {"$eq": "application/pdf"}},
    "empty filter matches everything": {},
    "non-string scalars": {"hidden": False, "count": 0},
    "null value": {"deletedAt": None},
    "date comparison": {"createdAt": {"$gte": "2024-01-01T00:00:00.000Z"}},
    "single-element $in": {"type": {"$in": ["file"]}},
    "$exists": {"metadata.user.pii": {"$exists": True}},
    "wildcard value": {"filename": "*jp?g"},
    "two operators on one field": {"size": {"$gt": 0, "$lt": 100}},
    "nested logicals": {
        "$and": [
            {"metadata.system.mimetype": {"$eq": "application/pdf"}},
            {"$or": [{"dir": "/a"}, {"dir": "/b"}]},
        ]
    },
    "deeply but legally nested": {"$and": [{"$or": [{"$and": [{"a": 1}]}]}]},
    # Documented on docs.dataloop.ai/docs/dql but absent from dtlpy's FiltersOperations enum.
    "$ieq (docs, not in the SDK enum)": {"name": {"$ieq": "foo"}},
    # Mongo-style embedded-document equality: the value is a literal document, not an operator map.
    "embedded document equality": {"metadata.system.refs": {"id": "abc123"}},
    # An operator this repo has never heard of. The platform's grammar is the contract, not our
    # list, so an unrecognised $op must pass through rather than be rejected.
    "unknown operator passes through": {"field": {"$somethingNew": "value"}},
    "unknown top-level logical passes through": {"$nor": [{"a": 1}]},
}


@pytest.mark.parametrize("shape", sorted(ACCEPTED), ids=sorted(ACCEPTED))
def test_valid_filters_are_not_rejected(shape):
    dql.validate_filter_body(ACCEPTED[shape])


def test_validate_data_filters_unwraps_a_top_level_filter_key():
    pdf_clause = {"metadata.system.mimetype": {"$eq": "application/pdf"}}
    dql.validate_data_filters({"filter": {"$and": [pdf_clause]}})


def test_validate_data_filters_accepts_a_bare_expression_with_no_filter_key():
    dql.validate_data_filters({"filename": "/incoming"})


def test_validate_data_filters_accepts_the_documented_sibling_keys():
    """`resource`, `page`, `pageSize`, `sort` and `join` are legal alongside `filter`."""
    dql.validate_data_filters(
        {
            "resource": "items",
            "page": 0,
            "pageSize": 100,
            "sort": {"createdAt": "descending"},
            "filter": {"$and": [{"hidden": False}]},
            "join": {
                "filter": {"$and": [{"type": {"$in": ["box"]}}]},
                "on": {"resource": "annotations", "local": "itemId", "forigen": "id"},
            },
        }
    )


# --- rejected shapes: only things that are provably wrong ---------------------------------


def test_rejects_a_non_dict_body():
    with pytest.raises(ValueError, match="must be a JSON object"):
        dql.validate_filter_body(["not", "a", "dict"])


def test_rejects_a_non_dict_data_filters():
    """Reachable if the function is reused outside pydantic, which coerces the type for us."""
    with pytest.raises(ValueError, match="must be a JSON object"):
        dql.validate_data_filters(5)


def test_rejects_a_logical_operator_given_a_dict_instead_of_a_list():
    with pytest.raises(ValueError, match="must be a non-empty list"):
        dql.validate_filter_body({"$and": {"field": "value"}})


def test_rejects_a_logical_operator_given_an_empty_list():
    with pytest.raises(ValueError, match="must be a non-empty list"):
        dql.validate_filter_body({"$or": []})


def test_rejects_an_empty_operator_map():
    with pytest.raises(ValueError, match="empty operator object"):
        dql.validate_filter_body({"field": {}})


def test_rejects_mixing_operators_and_literal_keys():
    """Either the value is an operator map or it is a literal document; it cannot be both."""
    with pytest.raises(ValueError, match="mixes operators with literal keys"):
        dql.validate_filter_body({"field": {"$eq": "a", "nested": "b"}})


@pytest.mark.parametrize("operator", ["$in", "$nin"])
def test_rejects_list_operators_given_a_non_list(operator):
    with pytest.raises(ValueError, match=f"\\{operator} needs a list"):
        dql.validate_filter_body({"field": {operator: "not-a-list"}})


def test_rejects_exists_given_a_non_boolean():
    with pytest.raises(ValueError, match="\\$exists needs true or false"):
        dql.validate_filter_body({"field": {"$exists": "yes"}})


def test_rejects_a_filter_nested_past_the_depth_cap():
    """A pathologically nested filter must raise ValueError, not RecursionError.

    An LLM can emit this by accident, and RecursionError would surface as an opaque internal
    failure rather than the actionable message the tool contract promises.
    """
    body = {"a": 1}
    for _ in range(dql.MAX_DEPTH + 5):
        body = {"$and": [body]}

    with pytest.raises(ValueError, match="nested more than"):
        dql.validate_filter_body(body)


def test_error_message_names_the_nested_path():
    with pytest.raises(ValueError, match=r"filter\.\$and\[1\]\.field"):
        dql.validate_filter_body({"$and": [{"a": "b"}, {"field": {"$in": "not-a-list"}}]})


# --- upstream pins -------------------------------------------------------------------------


def test_known_operators_is_a_superset_of_dtlpys_own_enum():
    """dtlpy's enum is a lower bound on what the platform accepts, never an upper bound.

    `Filters.add()` can only emit these, but `custom_filter` bypasses `add()` entirely, so raw DQL
    is bound by the platform's grammar instead. This pins the relationship rather than the set: if
    dtlpy *adds* an operator, our hint list must grow to match; if it removes one, that is fine.
    Deliberately not an equality assertion -- `$ieq` is documented by DOE and absent here.
    """
    sdk = {f"${operation.value}" for operation in dl.FiltersOperations}

    assert sdk <= dql.KNOWN_OPERATORS, f"dtlpy gained operators we do not list: {sdk - dql.KNOWN_OPERATORS}"


def test_documented_platform_operators_are_known():
    """Operators DOE's DQL reference lists, including ones dtlpy's enum omits."""
    documented = {"$and", "$or", "$eq", "$ieq", "$ne", "$gt", "$gte", "$lt", "$lte", "$in", "$exists"}

    assert documented <= dql.KNOWN_OPERATORS
