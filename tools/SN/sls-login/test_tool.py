from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from shed import ToolContext, ToolError
from shed.registry import load_tool, load_tool_module

module = load_tool_module("sls-login")
sls_module = load_tool_module("sls-logs")
TOOL = load_tool("sls-login")
CTX = ToolContext(cwd=Path.cwd(), log=lambda _: None)
NOW = 4_000_000_000  # epoch seconds, year 2096: a saved session stays valid whenever the tests run
URL = "https://sso.example.com/portal"
READER = "acs:ram::1234567890123456:role/log-reader"
ADMIN = "acs:ram::1234567890123456:role/admin"
PROVIDER = "acs:ram::1234567890123456:saml-provider/company-sso"


def saml(*roles: str, duration: int | None = None) -> str:
    """A base64 SAML response offering `roles`, as the single sign-on would post it."""
    values = "".join(f"<saml:AttributeValue>{role},{PROVIDER}</saml:AttributeValue>" for role in roles)
    allowed = (
        f'<saml:Attribute Name="{module.DURATION_ATTRIBUTE}">'
        f"<saml:AttributeValue>{duration}</saml:AttributeValue></saml:Attribute>"
        if duration
        else ""
    )
    xml = (
        '<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
        'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"><saml:Assertion><saml:AttributeStatement>'
        f'<saml:Attribute Name="{module.ROLE_ATTRIBUTE}">{values}</saml:Attribute>{allowed}'
        "</saml:AttributeStatement></saml:Assertion></samlp:Response>"
    )
    return base64.b64encode(xml.encode()).decode()


APP_PAGE = "https://sso.example.com/app/alibaba/sso"
AGENT = "Mozilla/5.0 Chrome/159.0.0.0"


class FakeBrowser:
    """Stands in for the Chromium: a posted SAML response, or None for a sign-in that never came."""

    def __init__(self, response: str | None, posted_from: str = APP_PAGE) -> None:
        self.response = response
        self.posted_from = posted_from
        self.opened: list[dict[str, Any]] = []

    def capture(
        self, url: str, state: Path, timeout: float, *, headed: bool, user_agent: str | None = None
    ) -> Any:
        self.opened.append(
            {"url": url, "state": state, "timeout": timeout, "headed": headed, "user_agent": user_agent}
        )
        if self.response is None:
            return None
        return module.Capture(self.response, self.posted_from, AGENT)


class FakeSts:
    """Stands in for the AssumeRoleWithSAML call and records what it was asked."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, role_arn: str, provider_arn: str, saml_response: str, duration: int | None) -> Any:
        self.calls.append({"role": role_arn, "provider": provider_arn, "duration": duration})
        ends = datetime.fromtimestamp(NOW + (duration or 3600), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {
            "AccessKeyId": "STS.id",
            "AccessKeySecret": "secret",
            "SecurityToken": "token",
            "Expiration": ends,
        }


@pytest.fixture
def sts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeSts:
    """Keep the run away from the real ~/.config files, the real clock, the browser and the network."""
    fake = FakeSts()
    monkeypatch.setattr(sls_module, "ENV_FILE", tmp_path / "absent.env")
    monkeypatch.setattr(sls_module, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setenv("SHED_BROWSER_DIR", str(tmp_path / "browser"))
    monkeypatch.delenv("SLS_LOGIN_URL", raising=False)
    monkeypatch.delenv("SLS_AUTH", raising=False)
    monkeypatch.setattr(module, "now", lambda: NOW)
    monkeypatch.setattr(module, "assume_role", fake)
    return fake


def sign_in(monkeypatch: pytest.MonkeyPatch, response: str | None, **flags: Any) -> tuple[Any, FakeBrowser]:
    fake = FakeBrowser(response)
    monkeypatch.setattr(module, "browser", fake)
    return TOOL.run(TOOL.input(**flags), CTX), fake


def expire_the_session() -> None:
    sls_module.save_session(sls_module.Credentials("old", "old", "old"), 1)


def test_a_sign_in_saves_a_session_the_log_tools_use(
    monkeypatch: pytest.MonkeyPatch, sts: FakeSts, tmp_path: Path
) -> None:
    result, fake = sign_in(monkeypatch, saml(READER), url=URL)
    assert (result.role, result.minutes, result.expires) == ("log-reader", 60, "2096-10-02 16:06:40")
    assert sts.calls == [{"role": READER, "provider": PROVIDER, "duration": None}]
    assert sls_module.credentials() == sls_module.Credentials("STS.id", "secret", "token")
    folder = tmp_path / "browser" / "sls-login"
    assert fake.opened == [
        {"url": URL, "state": folder / "state.json", "timeout": 300, "headed": True, "user_agent": None}
    ]
    assert folder.stat().st_mode & 0o777 == 0o700
    assert TOOL.render is not None
    assert "Signed in as log-reader" in TOOL.render(result)
    assert "token" not in result.model_dump_json()


def test_a_sign_in_leaves_what_a_renewal_needs(
    monkeypatch: pytest.MonkeyPatch, sts: FakeSts, tmp_path: Path
) -> None:
    result, _ = sign_in(monkeypatch, saml(READER), url=URL)
    note = tmp_path / "browser" / "sls-login" / "renewal.json"
    assert json.loads(note.read_text()) == {
        "address": APP_PAGE,
        "role": "log-reader",
        "user_agent": AGENT,
        "retry_after": 0,
    }
    assert note.stat().st_mode & 0o777 == 0o600
    assert result.renews is True
    assert TOOL.render is not None
    assert "renew by themselves" in TOOL.render(result)


def test_a_sign_in_posted_from_a_page_with_no_address_cannot_renew(
    monkeypatch: pytest.MonkeyPatch, sts: FakeSts, tmp_path: Path
) -> None:
    sign_in(monkeypatch, saml(READER), url=URL)
    fake = FakeBrowser(saml(READER), posted_from="about:blank")
    monkeypatch.setattr(module, "browser", fake)
    result = TOOL.run(TOOL.input(url=URL), CTX)
    assert result.renews is False
    assert not (tmp_path / "browser" / "sls-login" / "renewal.json").exists()
    assert TOOL.render is not None
    assert "Run `shed sls-login` again after that." in TOOL.render(result)


def test_renew_signs_in_again_with_no_window(
    monkeypatch: pytest.MonkeyPatch, sts: FakeSts, tmp_path: Path
) -> None:
    sign_in(monkeypatch, saml(READER, ADMIN), url=URL, role="reader")
    expire_the_session()
    fake = FakeBrowser(saml(READER, ADMIN))
    monkeypatch.setattr(module, "browser", fake)

    module.renew()

    assert fake.opened == [
        {
            "url": APP_PAGE,
            "state": tmp_path / "browser" / "sls-login" / "state.json",
            "timeout": module.RENEW_TIMEOUT_S,
            "headed": False,
            "user_agent": AGENT,
        }
    ]
    assert sts.calls[-1]["role"] == READER
    assert sls_module.load_session() == sls_module.Credentials("STS.id", "secret", "token")


def test_renew_leaves_a_live_session_alone(monkeypatch: pytest.MonkeyPatch, sts: FakeSts) -> None:
    sign_in(monkeypatch, saml(READER), url=URL)
    fake = FakeBrowser(saml(READER))
    monkeypatch.setattr(module, "browser", fake)
    module.renew()
    assert fake.opened == []


def test_renew_needs_an_earlier_sign_in(monkeypatch: pytest.MonkeyPatch, sts: FakeSts) -> None:
    expire_the_session()
    fake = FakeBrowser(saml(READER))
    monkeypatch.setattr(module, "browser", fake)
    with pytest.raises(ToolError, match="there is no earlier sign-in to renew from"):
        module.renew()
    assert fake.opened == []


def test_a_failed_renewal_is_not_tried_again_for_ten_minutes(
    monkeypatch: pytest.MonkeyPatch, sts: FakeSts
) -> None:
    sign_in(monkeypatch, saml(READER), url=URL)
    expire_the_session()
    fake = FakeBrowser(None)
    monkeypatch.setattr(module, "browser", fake)

    with pytest.raises(ToolError, match="the single sign-on asked for a person to sign in"):
        module.renew()
    with pytest.raises(ToolError, match="the last try failed, and the next one is not before 15:16"):
        module.renew()
    assert len(fake.opened) == 1

    monkeypatch.setattr(module, "now", lambda: NOW + module.RENEW_RETRY_S + 1)
    fake.response = saml(READER)
    module.renew()
    assert len(fake.opened) == 2
    assert sls_module.load_session() == sls_module.Credentials("STS.id", "secret", "token")


def test_a_role_named_exactly_wins_over_one_that_contains_the_name() -> None:
    longer = "acs:ram::1234567890123456:role/admin-readonly"
    assert module.choose_role([(ADMIN, PROVIDER), (longer, PROVIDER)], "admin") == (ADMIN, PROVIDER)


def test_the_session_lasts_as_long_as_the_sign_in_allows(
    monkeypatch: pytest.MonkeyPatch, sts: FakeSts
) -> None:
    result, _ = sign_in(monkeypatch, saml(READER, duration=14_400), url=URL)
    assert sts.calls[0]["duration"] == 14_400
    assert result.minutes == 240


def test_minutes_overrides_the_allowed_length(monkeypatch: pytest.MonkeyPatch, sts: FakeSts) -> None:
    sign_in(monkeypatch, saml(READER, duration=14_400), url=URL, minutes=30)
    assert sts.calls[0]["duration"] == 1_800


def test_the_sign_in_page_comes_from_the_setting(monkeypatch: pytest.MonkeyPatch, sts: FakeSts) -> None:
    monkeypatch.setenv("SLS_LOGIN_URL", "https://sso.example.com/from-setting")
    _, fake = sign_in(monkeypatch, saml(READER))
    assert fake.opened[0]["url"] == "https://sso.example.com/from-setting"


def test_a_later_sign_in_opens_the_page_the_last_one_was_posted_from(
    monkeypatch: pytest.MonkeyPatch, sts: FakeSts
) -> None:
    monkeypatch.setenv("SLS_LOGIN_URL", "https://sso.example.com/from-setting")
    sign_in(monkeypatch, saml(READER))
    _, later = sign_in(monkeypatch, saml(READER))
    assert later.opened[0]["url"] == APP_PAGE
    _, chosen = sign_in(monkeypatch, saml(READER), url=URL)
    assert chosen.opened[0]["url"] == URL


def test_no_sign_in_page_says_where_to_set_it(monkeypatch: pytest.MonkeyPatch, sts: FakeSts) -> None:
    with pytest.raises(ToolError, match="No sign-in page: pass --url"):
        sign_in(monkeypatch, saml(READER))


def test_a_sign_in_page_must_be_https(monkeypatch: pytest.MonkeyPatch, sts: FakeSts) -> None:
    with pytest.raises(ToolError, match="must be an https:// address"):
        sign_in(monkeypatch, saml(READER), url="http://sso.example.com/")


def test_a_closed_window_is_reported(monkeypatch: pytest.MonkeyPatch, sts: FakeSts) -> None:
    with pytest.raises(ToolError, match="No sign-in arrived within 300s"):
        sign_in(monkeypatch, None, url=URL)
    assert not sls_module.SESSION_FILE.exists()


def test_several_roles_need_a_choice(monkeypatch: pytest.MonkeyPatch, sts: FakeSts) -> None:
    with pytest.raises(ToolError, match=r"offers 2 roles \(log-reader, admin\); pass --role"):
        sign_in(monkeypatch, saml(READER, ADMIN), url=URL)
    result, _ = sign_in(monkeypatch, saml(READER, ADMIN), url=URL, role="READ")
    assert result.role == "log-reader"
    with pytest.raises(ToolError, match=r"no role matching 'ops' \(offered: log-reader, admin\)"):
        sign_in(monkeypatch, saml(READER, ADMIN), url=URL, role="ops")


def test_a_response_without_a_role_is_reported(monkeypatch: pytest.MonkeyPatch, sts: FakeSts) -> None:
    with pytest.raises(ToolError, match="named no Alibaba Cloud role"):
        sign_in(monkeypatch, saml(), url=URL)


def test_an_unreadable_response_is_reported(monkeypatch: pytest.MonkeyPatch, sts: FakeSts) -> None:
    with pytest.raises(ToolError, match="SAML response that cannot be read"):
        sign_in(monkeypatch, base64.b64encode(b"<not-closed").decode(), url=URL)


def test_role_and_provider_are_read_in_either_order() -> None:
    xml = (
        '<Response xmlns:a="urn:oasis:names:tc:SAML:2.0:assertion"><a:Assertion>'
        f'<a:Attribute Name="{module.ROLE_ATTRIBUTE}"><a:AttributeValue> {PROVIDER} , {READER} '
        "</a:AttributeValue></a:Attribute></a:Assertion></Response>"
    )
    assert module.read_assertion(base64.b64encode(xml.encode()).decode()) == ([(READER, PROVIDER)], None)


class FakeHttpResponse:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        return self._body


def test_the_exchange_is_an_anonymous_form_post(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}

    def post(url: str, *, data: dict[str, str], timeout: float) -> FakeHttpResponse:
        sent.update(data, url=url)
        return FakeHttpResponse(200, {"Credentials": {"AccessKeyId": "STS.id"}})

    monkeypatch.setattr("httpx.post", post)
    assert module.assume_role(READER, PROVIDER, "c2FtbA==", 1_800) == {"AccessKeyId": "STS.id"}
    assert sent["url"] == f"https://{module.STS_ENDPOINT}/"
    assert (sent["Action"], sent["Version"], sent["Format"]) == ("AssumeRoleWithSAML", "2015-04-01", "JSON")
    assert (sent["RoleArn"], sent["SAMLProviderArn"], sent["SAMLAssertion"]) == (READER, PROVIDER, "c2FtbA==")
    assert sent["DurationSeconds"] == "1800"
    assert "AccessKeyId" not in sent and "Signature" not in sent
    assert datetime.strptime(sent["Timestamp"], "%Y-%m-%dT%H:%M:%SZ")


def test_a_refused_exchange_names_the_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    refusal = {"Code": "EntityNotExist.SAMLProvider", "Message": "Can not find SAML provider"}
    monkeypatch.setattr("httpx.post", lambda *_, **__: FakeHttpResponse(404, refusal))
    with pytest.raises(ToolError, match=r"refused the sign-in \(EntityNotExist.SAMLProvider: Can not find"):
        module.assume_role(READER, PROVIDER, "c2FtbA==", None)


def test_an_unreachable_exchange_is_a_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def post(*_: Any, **__: Any) -> FakeHttpResponse:
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr("httpx.post", post)
    with pytest.raises(ToolError, match="Could not reach Alibaba Cloud STS"):
        module.assume_role(READER, PROVIDER, "c2FtbA==", None)


def test_an_answer_without_credentials_is_reported(monkeypatch: pytest.MonkeyPatch, sts: FakeSts) -> None:
    monkeypatch.setattr(module, "assume_role", lambda *_: {"AccessKeyId": "STS.id"})
    with pytest.raises(ToolError, match="without usable credentials"):
        sign_in(monkeypatch, saml(READER), url=URL)
    assert not sls_module.SESSION_FILE.exists()
