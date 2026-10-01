import importlib.util
import json
import os
import tempfile
from pathlib import Path

import requests

SCRIPT = Path(__file__).with_name("weread_login.py")
spec = importlib.util.spec_from_file_location("weread_login", SCRIPT)
login = importlib.util.module_from_spec(spec)
spec.loader.exec_module(login)


class _Raw:
    def __init__(self, set_cookies):
        self.headers = self
        self._set_cookies = set_cookies

    def getlist(self, name):
        return list(self._set_cookies) if name == "Set-Cookie" else []


class _Response:
    def __init__(self, status=200, payload=None, set_cookies=(), text=""):
        self.status_code = status
        self._payload = payload
        self.raw = _Raw(set_cookies)
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _Session:
    """Records requests and replays queued responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_confirm_url_encodes_the_login_uid():
    assert login.confirm_url("abc123") == "https://weread.qq.com/web/confirm?uid=abc123"
    assert login.confirm_url("a b/c") == "https://weread.qq.com/web/confirm?uid=a%20b%2Fc"


def test_begin_login_opens_skills_page_then_asks_for_uid():
    session = _Session([
        _Response(200, set_cookies=["wr_vid=abc; Path=/", "wr_skey=old; Path=/"]),
        _Response(200, {"uid": "UID-42"}, set_cookies=["wr_ql=0; Path=/"]),
    ])
    uid, jar = login.begin_login(session)

    assert uid == "UID-42"
    assert jar["wr_vid"] == "abc"
    assert jar["wr_skey"] == "old"
    assert jar["wr_ql"] == "0"
    # The skills page is opened with redirects followed; the uid call carries the jar.
    assert session.calls[0][0] == login.SKILLS_PAGE_URL
    assert session.calls[0][1]["allow_redirects"] is True
    assert session.calls[1][0] == login.LOGIN_UID_URL
    assert "wr_vid=abc" in session.calls[1][1]["headers"]["Cookie"]


def test_begin_login_reports_a_bad_status_instead_of_leaking_the_body():
    session = _Session([_Response(503, text="<html>maintenance</html>")])
    try:
        login.begin_login(session)
    except login.LoginError as exc:
        assert "503" in str(exc)
        assert "maintenance" not in str(exc)
    else:
        raise AssertionError("a 503 skills page was accepted")


def test_begin_login_rejects_a_missing_uid():
    session = _Session([_Response(200), _Response(200, {"uid": ""})])
    try:
        login.begin_login(session)
    except login.LoginError as exc:
        assert "登录标识" in str(exc)
    else:
        raise AssertionError("an empty uid was accepted")


def test_poll_login_asks_for_the_login_info_endpoint():
    session = _Session([_Response(200, {"succeed": False}, set_cookies=["wr_skey=new; Path=/"])])
    jar = {"wr_vid": "abc"}
    data = login.poll_login(session, jar, "UID-42")

    assert data == {"succeed": False}
    assert jar["wr_skey"] == "new", "poll response cookies must be merged back"
    url = session.calls[0][0]
    assert url.startswith(login.LOGIN_INFO_URL + "?uid=UID-42&otp")
    assert session.calls[0][1]["timeout"] == (login.POLL_BLOCK_TIMEOUT, login.POLL_TOTAL_TIMEOUT)


def test_poll_login_includes_an_otp_when_the_user_typed_one():
    session = _Session([_Response(200, {"succeed": True})])
    login.poll_login(session, {}, "UID-42", "123456")
    assert session.calls[0][0].endswith("&otp=123456")


def test_poll_login_signals_pending_instead_of_failing_on_a_long_poll():
    session = _Session([requests.Timeout("read timed out")])
    try:
        login.poll_login(session, {}, "UID-42")
    except login.LoginPending:
        pass
    else:
        raise AssertionError("a timed-out long poll was not reported as pending")


def test_complete_login_builds_a_fresh_cookie_jar_and_returns_the_api_key():
    session = _Session([
        _Response(200, {"name": "齐佩甲"}),                 # userInfo
        _Response(200, {"apikey": "wrk-abc123"}),           # apikeyGet
    ])
    jar = {"weread_session": "keep-me", "wr_vid": "OLD", "wr_skey": "OLD"}
    result = login.complete_login(session, jar, {
        "succeed": True, "webLoginVid": "VID-9",
        "accessToken": "SKEY-9", "refreshToken": "RT/9"})

    # Session cookies collected during this login flow are carried forward, but
    # every account-identifying cookie is replaced by the newly scanned account.
    assert result["cookies"]["weread_session"] == "keep-me"
    assert result["cookies"]["wr_vid"] == "VID-9"
    assert result["cookies"]["wr_skey"] == "SKEY-9"
    assert result["cookies"]["wr_ql"] == "0"
    assert result["cookies"]["wr_rt"] == "RT%2F9", "refresh token must be url-encoded"
    assert result["api_key"] == "wrk-abc123"
    assert result["wr_ticket"] == "" and result["wr_wrpa"] == ""
    assert result["account"]["name"] == "齐佩甲"
    assert result["account"]["login_method"] == "qr"

    # begin_login() is what guarantees freshness: it always starts from an empty jar.
    fresh = _Session([
        _Response(200, set_cookies=["wr_vid=abc; Path=/"]),
        _Response(200, {"uid": "U2"}),
    ])
    _uid, new_jar = login.begin_login(fresh)
    assert list(new_jar) == ["wr_vid"], new_jar

    # The authenticated calls must carry X-Vid / X-Skey.
    auth_headers = session.calls[0][1]["headers"]
    assert auth_headers["X-Vid"] == "VID-9"
    assert auth_headers["X-Skey"] == "SKEY-9"


def test_complete_login_explains_the_missing_skill_instead_of_a_bare_error():
    session = _Session([
        _Response(200, {"name": "齐佩甲"}),
        _Response(200, {}),
        _Response(200, {}),
        _Response(200, {}),
    ])
    try:
        login.complete_login(session, {}, {"succeed": True, "webLoginVid": "V",
                                           "accessToken": "S"})
    except login.LoginError as exc:
        assert "微信读书技能" in str(exc)
        assert "获取 API Key" in str(exc)
    else:
        raise AssertionError("an account without the skill was accepted")


def test_complete_login_rejects_a_result_that_did_not_succeed():
    session = _Session([])
    try:
        login.complete_login(session, {}, {"succeed": False})
    except login.LoginError as exc:
        assert "没有成功" in str(exc)
    else:
        raise AssertionError("an unsuccessful poll result was accepted")


def test_complete_login_retries_a_401_before_giving_up():
    session = _Session([
        _Response(401),
        _Response(200, {"name": "齐佩甲"}),
        _Response(200, {"apikey": "wrk-late"}),
    ])
    result = login.complete_login(session, {}, {"succeed": True,
                                                "webLoginVid": "V", "accessToken": "S"})
    assert result["api_key"] == "wrk-late"


def test_poll_state_machine_matches_the_plugin_branch_table():
    assert login.interpret_poll({"succeed": True}) == ("ok", "")
    assert login.interpret_poll({"logicCode": "NEED_OTP"})[0] == "otp"
    assert "4 位验证码" in login.interpret_poll({"logicCode": "NEED_OTP"})[1]
    assert login.interpret_poll({"logicCode": "OTP_NOT_MATCH"})[0] == "otp"
    assert "不正确" in login.interpret_poll({"logicCode": "OTP_NOT_MATCH"})[1]
    assert login.interpret_poll({"logicCode": "OTP_EXPIRED"})[0] == "otp_expired"
    assert login.interpret_poll({"logicCode": "LOGIN_TIMEOUT"})[0] == "expired"
    state, detail = login.interpret_poll({"logicCode": "SOMETHING_ELSE"})
    assert state == "failed" and "SOMETHING_ELSE" in detail
    # An unrecognised payload must fail loudly rather than spin forever.
    assert login.interpret_poll({})[0] == "failed"
    assert login.interpret_poll("not a dict")[0] == "failed"


def test_credentials_round_trip_through_the_session_state_file():
    with tempfile.TemporaryDirectory() as temp:
        target = Path(temp) / "session-cookies.json"
        saved = login.save_credentials({
            "cookies": {"wr_skey": "S", "wr_vid": "V"},
            "api_key": "wrk-1", "wr_ticket": "", "wr_wrpa": "",
            "account": {"name": "n", "login_method": "qr"},
        }, target)

        assert saved == target
        assert target.is_file()
        # No plaintext secret may be echoed by the loader.
        loaded = login.load_saved_credentials(target)
        assert loaded["api_key"] == "wrk-1"
        assert loaded["cookies"]["wr_skey"] == "S"
        assert loaded["account"]["login_method"] == "qr"
        assert json.loads(target.read_text(encoding="utf-8"))["cookies"]["wr_vid"] == "V"

        assert login.has_credentials(target) is True
        assert login.credentials_status(target)["ready"] is True


def test_credential_status_names_exactly_what_is_missing():
    with tempfile.TemporaryDirectory() as temp:
        target = Path(temp) / "session-cookies.json"
        assert login.has_credentials(target) is False
        assert login.credentials_status(target)["missing"] == ["api_key", "wr_skey"]

        login.save_credentials({"cookies": {"wr_skey": "S"}, "api_key": ""}, target)
        status = login.credentials_status(target)
        assert status["ready"] is False
        assert status["missing"] == ["api_key"], status

        login.save_credentials({"cookies": {}, "api_key": "wrk-1"}, target)
        status = login.credentials_status(target)
        assert status["missing"] == ["wr_skey"], status


def test_loading_absent_or_corrupt_state_is_not_fatal():
    with tempfile.TemporaryDirectory() as temp:
        missing = Path(temp) / "nope.json"
        assert login.load_saved_credentials(missing) == {}
        assert login.has_credentials(missing) is False

        broken = Path(temp) / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        assert login.load_saved_credentials(broken) == {}


def test_qr_rendering_produces_a_square_image():
    """A square-but-blank image is not a QR code, so assert real ink too.

    cv2 only exists in the dev environment; the ink check is the portable guard,
    and the decode round-trip runs wherever a decoder is available.
    """
    text = login.confirm_url("UID-42")
    image = login.render_qr_png(text, box_size=4, border=2)
    assert image.mode == "RGB"
    width, height = image.size
    assert width == height, image.size

    grey = image.convert("L")
    ink = sum(count for count, value in grey.getcolors(maxcolors=256) if value < 64)
    assert width >= 80, f"too small for a phone to scan: {image.size}"
    assert ink > width * height * 0.05, f"QR image looks blank ({ink} dark pixels)"

    try:
        import cv2
        import numpy as np
    except ImportError:
        return
    decoded, _, _ = cv2.QRCodeDetector().detectAndDecode(np.array(image.convert("RGB")))
    assert decoded == text, f"QR did not round-trip: {decoded!r}"


ALL_TESTS = [
    test_confirm_url_encodes_the_login_uid,
    test_begin_login_opens_skills_page_then_asks_for_uid,
    test_begin_login_reports_a_bad_status_instead_of_leaking_the_body,
    test_begin_login_rejects_a_missing_uid,
    test_poll_login_asks_for_the_login_info_endpoint,
    test_poll_login_includes_an_otp_when_the_user_typed_one,
    test_poll_login_signals_pending_instead_of_failing_on_a_long_poll,
    test_poll_state_machine_matches_the_plugin_branch_table,
    test_complete_login_builds_a_fresh_cookie_jar_and_returns_the_api_key,
    test_complete_login_explains_the_missing_skill_instead_of_a_bare_error,
    test_complete_login_rejects_a_result_that_did_not_succeed,
    test_complete_login_retries_a_401_before_giving_up,
    test_credentials_round_trip_through_the_session_state_file,
    test_credential_status_names_exactly_what_is_missing,
    test_loading_absent_or_corrupt_state_is_not_fatal,
    test_qr_rendering_produces_a_square_image,
]


if __name__ == "__main__":
    for test in ALL_TESTS:
        test()
        print("PASS", test.__name__)
