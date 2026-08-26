"""Bounding dtlpy's two different list shapes."""

from types import SimpleNamespace

from doe_mcp import paging


def serialize(entity):
    return {"id": entity}


def test_from_list_applies_page_window_and_limit():
    """datasets.list() returns everything it can find, so the cap has to bite here."""
    result = paging.from_list([f"d{i}" for i in range(120)], serialize, limit=4, page=2, page_size=10)

    assert result["returned"] == 4
    assert result["total"] == 120
    assert result["has_more"] is True
    assert result["page"] == 2
    assert result["page_size"] == 10
    assert result["items"] == [{"id": "d20"}, {"id": "d21"}, {"id": "d22"}, {"id": "d23"}]


def test_from_list_without_overflow():
    result = paging.from_list(["a", "b"], serialize, limit=50, page=0, page_size=50)

    assert (result["returned"], result["total"], result["has_more"], result["page"]) == (2, 2, False, 0)


def test_from_paged_uses_platform_metadata():
    paged = SimpleNamespace(items=["a", "b"], items_count=57, has_next_page=True, page_offset=3, page_size=17)

    result = paging.from_paged(paged, serialize, limit=50)

    assert result["returned"] == 2
    assert result["total"] == 57
    assert result["has_more"] is True
    assert result["page"] == 3
    assert result["page_size"] == 17


def test_from_paged_flags_more_when_page_itself_is_trimmed():
    """A page larger than the caller's limit still has to advertise the leftovers."""
    paged = SimpleNamespace(
        items=list("abcdef"), items_count=6, has_next_page=False, page_offset=0, page_size=6
    )

    result = paging.from_paged(paged, serialize, limit=2)

    assert result["returned"] == 2
    assert result["has_more"] is True


def test_mask_fields_returns_the_entity_unchanged_when_no_fields_requested():
    entity = {"id": "i1", "name": "n", "extra": "x"}

    assert paging.mask_fields(entity, None) == entity


def test_mask_fields_projects_down_to_the_requested_fields():
    entity = {"id": "i1", "name": "n", "extra": "x"}

    assert paging.mask_fields(entity, ["name"]) == {"id": "i1", "name": "n"}


def test_mask_fields_always_keeps_id_even_when_not_requested():
    """A leaned-down result still has to be addressable in a follow-up tool call."""
    entity = {"id": "i1", "name": "n"}

    assert paging.mask_fields(entity, []) == {"id": "i1"}


def test_from_list_applies_field_masking():
    result = paging.from_list(["a"], serialize, fields=["id"])

    assert result["items"] == [{"id": "a"}]


def test_from_paged_applies_field_masking():
    def wide_serialize(entity):
        return {"id": entity, "name": f"name-{entity}", "extra": "drop-me"}

    paged = SimpleNamespace(items=["a"], items_count=1, has_next_page=False, page_offset=0, page_size=10)

    result = paging.from_paged(paged, wide_serialize, fields=["name"])

    assert result["items"] == [{"id": "a", "name": "name-a"}]


def test_from_paged_tolerates_empty_page():
    paged = SimpleNamespace(items=None, items_count=0, has_next_page=False, page_offset=0, page_size=0)

    result = paging.from_paged(paged, serialize, limit=10)

    assert result == {
        "items": [],
        "returned": 0,
        "total": 0,
        "has_more": False,
        "page": 0,
        "page_size": 0,
        "scanned": None,
        "scan_limit": None,
        "scan_truncated": None,
    }
