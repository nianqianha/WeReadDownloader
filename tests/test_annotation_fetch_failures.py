"""Failed annotation requests must remain distinguishable from empty results."""
import contextlib
import io
import json
from unittest.mock import patch

import pytest
from requests import HTTPError
import test_checkpoint_modes as checkpoints

wd = checkpoints.wd

REAL_API = wd.WeRead


@pytest.mark.parametrize("failed_batch", [0, 1, 2])
def test_review_batches_preserve_successes_and_count_failures(monkeypatch, failed_batch):
    api = REAL_API.__new__(REAL_API)
    calls = []
    monkeypatch.setattr(wd.time, "sleep", lambda seconds: None)
    def gateway(endpoint, payload):
        if endpoint == "/book/underlines":
            return {"underlines": [{"range": f"{n}-{n+1}"} for n in range(31)]}
        calls.append(endpoint)
        if len(calls) == failed_batch:
            raise HTTPError("synthetic failure")
        return {"reviews": [{"range": "0-1", "pageReviews": []}]}
    api.gateway = gateway
    underlines, reviews = api.annotations("123", "1")
    assert len(calls) == 2
    assert len(reviews) == (1 if failed_batch else 2)
    assert underlines["annotation_fetch_failures"] == {"underlines": 0, "reviews": int(bool(failed_batch))}


@pytest.mark.parametrize("result", ["empty", "underlines-error", "bad-reviews"])
def test_legitimate_empty_results_are_not_request_failures(result):
    api = REAL_API.__new__(REAL_API)
    def gateway(endpoint, payload):
        if endpoint == "/book/underlines":
            if result == "underlines-error":
                raise HTTPError("synthetic failure")
            return {"underlines": [] if result == "empty" else [{"range": "0-1"}]}
        return {"reviews": None}
    api.gateway = gateway
    underlines, reviews = api.annotations("123", "1")
    expected = {"underlines": int(result == "underlines-error"), "reviews": int(result == "bad-reviews")}
    assert underlines["annotation_fetch_failures"] == expected
    assert reviews == []


def test_failed_requests_reach_manifest_and_retry_only_affected_chapters():
    case = checkpoints.CheckpointModesTests()
    case.setUp()
    try:
        api_class = wd.WeRead
        original = api_class.annotations
        def failed(self, book, uid, thoughts, underlines):
            data, reviews = original(self, book, uid, thoughts, underlines)
            if str(uid) == "1":
                data["annotation_fetch_failures"] = {"underlines": 0, "reviews": 1}
                reviews = []
            return data, reviews
        with patch.object(api_class, "annotations", failed), contextlib.redirect_stdout(io.StringIO()) as output:
            product = wd.export_book("123", case.output, {}, jobs=1)
        manifest = json.loads((product.parent / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["omissions"]["annotation_fetch_failures"] == {"underlines": 0, "reviews": 1}
        assert manifest["omissions"]["chapters_with_annotation_fetch_failures"] == 1
        emitted = next(line for line in output.getvalue().split('\n') if line.startswith("OMISSIONS "))
        assert json.loads(emitted.removeprefix("OMISSIONS ")) == manifest["omissions"]
        case.fetched.clear()
        case.export(True, True)
        assert case.fetched == ["1"]
        manifest = json.loads((product.parent / "manifest.json").read_text(encoding="utf-8"))
        assert not any(manifest["omissions"]["annotation_fetch_failures"].values())
    finally:
        case.doCleanups()


def test_failed_retry_preserves_previous_complete_product():
    case = checkpoints.CheckpointModesTests()
    case.setUp()
    try:
        case.export(True, True)
        folder = case.output / "Synthetic book-123"
        before = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
        (case.workspace / "chapter-uid-1.body").write_text("", encoding="utf-8")
        api_class = wd.WeRead
        original = api_class.annotations
        def failed(self, *args):
            data, reviews = original(self, *args)
            data["annotation_fetch_failures"] = {"underlines": 1, "reviews": 0}
            return data, []
        with patch.object(api_class, "annotations", failed), contextlib.redirect_stdout(io.StringIO()), \
                pytest.raises(RuntimeError, match="preserved the previous complete export"):
            wd.export_book("123", case.output, {}, jobs=1)
        assert {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()} == before
        case.fetched.clear()
        case.export(True, True)
        assert case.fetched == ["1"]
    finally:
        case.doCleanups()
