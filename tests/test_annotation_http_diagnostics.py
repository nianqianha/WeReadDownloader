"""HTTP diagnostics must help investigation without logging response content."""
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "weread"))
import weread_desktop as engine


@pytest.mark.parametrize("status", [401, 403, 426, 429, 500])
@pytest.mark.parametrize("endpoint", ["/book/underlines", "/book/readreviews"])
def test_annotation_http_status_is_logged_without_private_content(status, endpoint, capsys):
    response = requests.Response()
    response.status_code = status
    response._content = b"private-response-sentinel"
    response.url = "https://example.invalid/?private-query-sentinel"
    error = requests.HTTPError("private-exception-sentinel", response=response)

    def gateway(name, _params):
        if name == endpoint:
            raise error
        return {"underlines": [{"range": "1-2"}]}

    underlines, reviews = engine.WeRead.annotations(
        SimpleNamespace(gateway=gateway), "synthetic-book", 1)
    log = capsys.readouterr().err
    assert f"HTTPError, HTTP {status}" in log
    assert ("underlines unavailable" if endpoint.endswith("underlines") else "reviews batch at 0 failed") in log
    assert "sentinel" not in log and "https://" not in log
    assert reviews == []
    assert underlines["chapterUid"] == 1


@pytest.mark.parametrize("error", [requests.HTTPError("private-sentinel"), ValueError("private-sentinel")])
def test_annotation_errors_without_http_response_keep_only_exception_type(error):
    assert engine._annotation_error(error) == type(error).__name__
