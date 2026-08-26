"""Item tools: paging, the metadata allow-list, and per-file upload reporting."""

from types import SimpleNamespace

import pytest

from doe_mcp.tools import items


def fake_item(item_id="item-1", name="photo.jpg", metadata=None):
    return SimpleNamespace(
        id=item_id,
        name=name,
        dir="/",
        type="file",
        mimetype="image/jpeg",
        filename=f"/{name}",
        dataset_id="ds-1",
        annotated=False,
        annotations_count=0,
        created_at="2026-01-01",
        metadata=metadata if metadata is not None else {"user": {"source": "camera"}},
        platform_url="https://example/item-1",
        description=None,
    )


class FakeItems:
    def __init__(self, upload_result=None):
        self.list_filters = None
        self.uploaded: list[dict] = []
        self.updated = None
        self._upload_result = upload_result

    def list(self, filters=None):
        self.list_filters = filters
        return SimpleNamespace(
            items=[fake_item(), fake_item("item-2", "scan.pdf")],
            items_count=2,
            has_next_page=False,
            page_offset=getattr(filters, "page", None),
            page_size=getattr(filters, "page_size", None),
        )

    def get(self, item_id=None, **_):
        return fake_item(item_id)

    def upload(self, **kwargs):
        self.uploaded.append(kwargs)
        if callable(self._upload_result):
            return self._upload_result(**kwargs)
        return fake_item()

    def update(self, item=None, **kwargs):
        self.updated = {"item": item, **kwargs}
        return item


@pytest.fixture
def dataset(monkeypatch):
    fake = FakeItems()
    ds = SimpleNamespace(id="ds-1", name="images", items=fake)
    monkeypatch.setattr(items, "get_dataset", lambda project_id, dataset_id: ds)
    return ds


ARGS = {"project_id": "proj-1", "dataset_id": "ds-1"}


# --- listing ---------------------------------------------------------------------------


def test_item_list_is_bounded_and_reports_paging(dataset, call_tool):
    result = call_tool("item_list", {**ARGS, "page": 1, "page_size": 1, "limit": 1})

    assert result.is_error is False
    assert dataset.items.list_filters.page == 1
    assert dataset.items.list_filters.page_size == 1
    assert result.structured_content["returned"] == 1
    # Two entities came back but limit was 1, so the caller must be told to keep paging.
    assert result.structured_content["has_more"] is True


def test_item_list_hides_platform_system_metadata(dataset, call_tool):
    dataset.items.list = lambda filters=None: SimpleNamespace(
        items=[fake_item(metadata={"system": {"mimetype": "x"}, "user": {"keep": 1}})],
        items_count=1,
        has_next_page=False,
        page_offset=0,
        page_size=50,
    )

    result = call_tool("item_list", ARGS)

    metadata = result.structured_content["items"][0]["metadata"]
    assert metadata == {"user": {"keep": 1}}


def test_item_list_projects_down_to_requested_fields(dataset, call_tool):
    result = call_tool("item_list", {**ARGS, "fields": ["name"]})

    for entity in result.structured_content["items"]:
        assert set(entity) == {"id", "name"}


def test_item_get_returns_one_item(dataset, call_tool):
    result = call_tool("item_get", {**ARGS, "item_id": "item-9"})

    assert result.is_error is False
    assert result.structured_content["id"] == "item-9"


# --- upload ----------------------------------------------------------------------------


def test_upload_rejects_a_missing_file_before_calling_the_platform(dataset, call_tool):
    result = call_tool("item_upload", {**ARGS, "local_paths": ["/no/such/file.png"]})

    assert result.is_error is True
    assert "No such file" in result.content[0].text
    assert dataset.items.uploaded == []


def test_upload_reports_each_file_separately(dataset, call_tool, tmp_path):
    good = tmp_path / "good.png"
    bad = tmp_path / "bad.png"
    good.write_bytes(b"x")
    bad.write_bytes(b"x")

    def upload(local_path=None, **_):
        if local_path == str(bad):
            raise RuntimeError("quota exceeded")
        return fake_item()

    dataset.items._upload_result = upload

    result = call_tool("item_upload", {**ARGS, "local_paths": [str(good), str(bad)]})

    assert result.is_error is False
    body = result.structured_content
    # A partly-failed batch must not read as success.
    assert body["all_succeeded"] is False
    assert [entry["local_path"] for entry in body["uploaded"]] == [str(good)]
    assert body["failed"][0]["local_path"] == str(bad)
    assert "quota exceeded" in body["failed"][0]["error"]


def test_upload_treats_a_declined_file_as_a_failure(dataset, call_tool, tmp_path):
    path = tmp_path / "declined.png"
    path.write_bytes(b"x")
    # dtlpy returns None instead of raising when it declines a file.
    dataset.items._upload_result = lambda **_: None

    result = call_tool("item_upload", {**ARGS, "local_paths": [str(path)]})

    assert result.structured_content["all_succeeded"] is False
    assert result.structured_content["uploaded"] == []


def test_upload_refuses_system_metadata(dataset, call_tool, tmp_path):
    path = tmp_path / "a.png"
    path.write_bytes(b"x")

    result = call_tool(
        "item_upload",
        {**ARGS, "local_paths": [str(path)], "item_metadata": {"system": {"mimetype": "x"}}},
    )

    assert result.is_error is True
    assert dataset.items.uploaded == []


# --- update ----------------------------------------------------------------------------


def test_update_needs_something_to_change(dataset, call_tool):
    result = call_tool("item_update", {**ARGS, "item_id": "item-1"})

    assert result.is_error is True
    assert dataset.items.updated is None


def test_update_refuses_to_write_system_metadata(dataset, call_tool):
    result = call_tool(
        "item_update",
        {**ARGS, "item_id": "item-1", "metadata": {"system": {"mimetype": "forged"}}},
    )

    assert result.is_error is True
    assert "system" in result.content[0].text
    # Refused before any platform call, so nothing was sent.
    assert dataset.items.updated is None


def test_update_merges_user_metadata_and_never_sends_system_metadata(dataset, call_tool):
    result = call_tool("item_update", {**ARGS, "item_id": "item-1", "metadata": {"reviewed": True}})

    assert result.is_error is False
    sent = dataset.items.updated
    # Writes land under `user`, which is where every filter this server builds looks —
    # model_set_subset's review marker queries metadata.user.reviewed. A top-level write
    # produced a marker no filter could match; live smoke caught it.
    assert sent["item"].metadata["user"]["reviewed"] is True
    assert "reviewed" not in sent["item"].metadata
    # The pre-existing user metadata survives the merge.
    assert sent["item"].metadata["user"]["source"] == "camera"
    # dtlpy would persist metadata['system'] if this flag were ever passed.
    assert "system_metadata" not in sent


def test_update_renames_via_the_entity_not_a_patch_dict(dataset, call_tool):
    call_tool("item_update", {**ARGS, "item_id": "item-1", "name": "renamed.jpg"})

    sent = dataset.items.updated
    assert sent["item"].name == "renamed.jpg"
    # Items.update raises if update_values is combined with a specific item, so the tool must
    # mutate the entity rather than forward a patch.
    assert "update_values" not in sent


# --- dataset_profile: bounded, honest counts (no aggregation API exists) -----------------


class ProfileItems:
    """Pages of items, so the bounded scan can be driven past its cap."""

    def __init__(self, pages):
        self._pages = pages
        self.requested_pages = []

    def list(self, filters=None):
        index = getattr(filters, "page", 0)
        self.requested_pages.append(index)
        page = self._pages[index] if index < len(self._pages) else []
        return SimpleNamespace(items=page, has_next_page=index < len(self._pages) - 1)


def profile_item(name, mimetype, directory="/", annotated=False):
    return SimpleNamespace(
        id=f"i-{name}", name=name, mimetype=mimetype, type="file", dir=directory, annotated=annotated
    )


def profile_dataset(monkeypatch, pages, items_count=None):
    repo = ProfileItems(pages)
    ds = SimpleNamespace(id="ds-1", name="docs", items=repo, items_count=items_count, annotations_count=7)
    monkeypatch.setattr(items, "get_dataset", lambda project_id, dataset_id: ds)
    return ds


def test_profile_counts_file_types_folders_and_annotation_coverage(monkeypatch, call_tool):
    profile_dataset(
        monkeypatch,
        [
            [
                profile_item("a.pdf", "application/pdf", annotated=True),
                profile_item("b.pdf", "application/pdf"),
                profile_item("c.jpg", "image/jpeg", "/photos"),
            ]
        ],
        items_count=3,
    )

    body = call_tool("dataset_profile", ARGS).structured_content

    assert body["file_types"] == {"application/pdf": 2, "image/jpeg": 1}
    assert body["directories"] == {"/": 2, "/photos": 1}
    assert body["annotated_items"] == 1
    assert body["unannotated_items"] == 2
    assert body["sample_filenames"][:1] == ["a.pdf"]


def test_profile_reports_a_complete_scan_as_untruncated(monkeypatch, call_tool):
    profile_dataset(monkeypatch, [[profile_item("a.pdf", "application/pdf")]], items_count=1)

    body = call_tool("dataset_profile", ARGS).structured_content

    assert body["scan_truncated"] is False
    # Nothing to warn about when the scan saw everything.
    assert body["caveat"] is None


def test_profile_says_so_when_the_counts_are_only_a_sample(monkeypatch, call_tool):
    # Two full pages with more behind them, scanned under a cap of one page.
    pages = [[profile_item(f"{n}.pdf", "application/pdf") for n in range(100)] for _ in range(3)]
    profile_dataset(monkeypatch, pages, items_count=5000)

    body = call_tool("dataset_profile", {**ARGS, "max_scan": 100}).structured_content

    assert body["scan_truncated"] is True
    assert body["scanned"] == 100
    # The dangerous reading is "this dataset has 100 PDFs". The caveat must prevent it.
    assert "not the whole dataset" in body["caveat"]
    assert "5000" in body["caveat"]


def test_profile_never_claims_a_census_it_did_not_take(monkeypatch, call_tool):
    pages = [[profile_item(f"{n}.jpg", "image/jpeg") for n in range(100)] for _ in range(2)]
    profile_dataset(monkeypatch, pages, items_count=200)

    body = call_tool("dataset_profile", {**ARGS, "max_scan": 100}).structured_content

    # file_types sums to what was scanned, not to items_count.
    assert sum(body["file_types"].values()) == body["scanned"] == 100
    assert body["dataset"]["items_count"] == 200
