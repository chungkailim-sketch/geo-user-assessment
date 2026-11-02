"""Full PDF report by email: API flow, full audit on the fixture site, PDF content and SMTP delivery."""

import smtplib
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.report import jobs as report_jobs
from app.report import mailer
from app.report.pdf import OFFERING_URL, render_report_pdf
from tests.integration.test_api import SMART_CLEANING, RoutingClaude, wait_for

pytestmark = pytest.mark.integration

ANALYSIS = {"experience": 12, "expertise": 15, "authoritativeness": 9, "trustworthiness": 20, "topical_modifier": 5,
            "eeat_strengths": ["产品数据具体"], "eeat_weaknesses": ["缺少作者署名"],
            "executive_summary": "Acme Clean 的GEO综合表现中等，内容数据扎实，但结构化数据与站外权威仍有明显差距。" * 2,
            "quick_wins": ["创建 llms.txt", "补充 FAQPage", "完善 sameAs", "增加作者署名", "为图片补充 alt"],
            "plan": [{"theme": f"第{i}周", "items": ["任务一", "任务二"]} for i in range(1, 5)]}


@pytest.fixture
def email_settings(test_settings):
    return test_settings.model_copy(update={"smtp_host": "smtp.example", "smtp_from": "geo@rf.example",
                                            "report_bcc": "team@rf.example, bad"})


@pytest.fixture
def sent(monkeypatch):
    messages = []

    async def fake_send(settings, message):
        messages.append(message)

    monkeypatch.setattr(report_jobs, "send_report", fake_send)
    return messages


def claude_with_analysis():
    claude = RoutingClaude(SMART_CLEANING)
    claude.routes.append(("senior GEO (Generative Engine Optimization) consultant", ANALYSIS))
    return claude


def wait_report(client: TestClient, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/report/{job_id}").json()
        if body["status"] in ("sent", "error"):
            return body
        time.sleep(0.05)
    raise AssertionError("report did not finish")


def run_audit(client: TestClient) -> str:
    job_id = client.post("/api/audit", json={"url": "www.acmeclean.example"}).json()["job_id"]
    assert wait_for(client, job_id)["status"] == "done"
    return job_id


def test_report_is_built_and_emailed(email_settings, mock_transport, sent):
    claude = claude_with_analysis()
    with TestClient(create_app(email_settings, claude_client=claude, transport=mock_transport)) as client:
        assert client.get("/api/report/config").json() == {"enabled": True}
        audit_id = run_audit(client)
        start = client.post("/api/report", json={"audit_id": audit_id, "email": "  ck@brand.example "})
        assert start.status_code == 200, start.text
        assert wait_report(client, start.json()["job_id"]) == {"status": "sent", "stage": "报告已发送", "progress": 100, "error": None}
        # A second send reuses the full audit instead of re-crawling and re-asking Claude.
        again = client.post("/api/report", json={"audit_id": audit_id, "email": "other@brand.example"})
        assert wait_report(client, again.json()["job_id"])["status"] == "sent"
    assert claude.count("senior GEO") == 1
    message = sent[0]
    assert message["To"] == "ck@brand.example" and "Acme Clean" in message["Subject"]
    assert message["From"].endswith("<geo@rf.example>")
    attachment = next(message.iter_attachments())
    pdf = attachment.get_content()
    assert attachment.get_filename() == "GEO诊断报告-Acme-Clean.pdf" and pdf.startswith(b"%PDF")
    assert OFFERING_URL.encode() in pdf  # last-page link annotation
    assert OFFERING_URL in message.get_body(("html",)).get_content()


def test_report_send_limit_and_validation(email_settings, mock_transport, sent):
    settings = email_settings.model_copy(update={"report_max_sends_per_audit": 1})
    with TestClient(create_app(settings, claude_client=claude_with_analysis(), transport=mock_transport)) as client:
        audit_id = run_audit(client)
        assert client.post("/api/report", json={"audit_id": audit_id, "email": "not-an-email"}).status_code == 422
        unknown = client.post("/api/report", json={"audit_id": "0" * 32, "email": "a@b.example"})
        assert unknown.status_code == 400 and "未找到" in unknown.json()["detail"]
        first = client.post("/api/report", json={"audit_id": audit_id, "email": "a@b.example"})
        wait_report(client, first.json()["job_id"])
        capped = client.post("/api/report", json={"audit_id": audit_id, "email": "a@b.example"})
        assert capped.status_code == 400 and "上限" in capped.json()["detail"]
        assert client.get("/api/report/" + "f" * 32).status_code == 404


def test_report_disabled_without_smtp(test_settings, mock_transport):
    with TestClient(create_app(test_settings, transport=mock_transport)) as client:
        assert client.get("/api/report/config").json() == {"enabled": False}
        audit_id = run_audit(client)
        response = client.post("/api/report", json={"audit_id": audit_id, "email": "a@b.example"})
    assert response.status_code == 400 and "尚未配置" in response.json()["detail"]


def test_report_without_claude_uses_heuristics_and_smtp_errors_are_reported(email_settings, mock_transport, monkeypatch):
    async def failing_send(settings, message):
        raise mailer.EmailError("邮件发送失败，请稍后重试。")

    monkeypatch.setattr(report_jobs, "send_report", failing_send)
    with TestClient(create_app(email_settings, transport=mock_transport)) as client:
        audit_id = run_audit(client)
        job = client.post("/api/report", json={"audit_id": audit_id, "email": "a@b.example"}).json()["job_id"]
        result = wait_report(client, job)
        report = client.app.state.reports.cache.get(f"{report_jobs.FULL_KEY}{audit_id}", 3600)
    assert result["status"] == "error" and result["error"] == "邮件发送失败，请稍后重试。"
    assert report["ai_written"] is False and report["eeat"]["source"] == "heuristic"
    assert len(report["plan"]) == 4 and report["executive_summary"].startswith("Acme Clean")
    assert {c["key"] for c in report["categories"]} == {"citability", "brand", "eeat", "technical", "schema", "platform"}
    assert report["geo_score"] == round(sum(c["score"] * c["weight"] for c in report["categories"]), 1)
    assert render_report_pdf(report).startswith(b"%PDF")  # renders without any AI-written text


class FakeSMTP:
    instances: list = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.calls = host, port, []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user))

    def send_message(self, message, from_addr=None, to_addrs=None):
        self.calls.append(("send", from_addr, to_addrs))
        return {}


async def test_smtp_delivery_uses_starttls_login_and_bcc(email_settings, monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    settings = email_settings.model_copy(update={"smtp_username": " u ", "smtp_password": "p", "smtp_from": " geo@rf.example "})
    message = mailer.build_message(settings, "ck@brand.example", "Acme\r\nBcc: x@evil.example", "https://a.example", 61.2, b"%PDF")
    await mailer.send_report(settings, message)
    smtp = FakeSMTP.instances[-1]
    assert smtp.calls == ["starttls", ("login", "u"), ("send", "geo@rf.example", ["ck@brand.example", "team@rf.example"])]
    assert "\n" not in message["Subject"] and message["Bcc"] is None


def refusing(code: int, reply: bytes):
    class Refusing(FakeSMTP):
        def send_message(self, message, from_addr=None, to_addrs=None):
            raise smtplib.SMTPRecipientsRefused({to_addrs[0]: (code, reply)})
    return Refusing


@pytest.mark.parametrize("code,reply,username,expected", [
    (550, b"5.1.1 User unknown", "u", "请检查邮箱是否正确"),
    (550, b"5.7.1 Unable to relay", "", "发件账号未登录验证"),
    (550, b"5.7.64 TenantAttribution; Relay Access Denied", "u", "发件服务器配置或权限问题"),
    (451, b"4.3.0 Try again later", "u", "暂时无法投递"),
])
async def test_smtp_refusals_are_explained_and_logged(email_settings, monkeypatch, caplog, code, reply, username, expected):
    monkeypatch.setattr(smtplib, "SMTP", refusing(code, reply))
    settings = email_settings.model_copy(update={"smtp_username": username})
    message = mailer.build_message(settings, "visitor@brand.example", "Acme", "https://a.example", 50, b"%PDF")
    with pytest.raises(mailer.EmailError, match=expected):
        await mailer.send_report(settings, message)
    assert reply.decode() in caplog.text and "***@brand.example" in caplog.text and "visitor@" not in caplog.text


@pytest.mark.parametrize("exc,expected", [
    (smtplib.SMTPSenderRefused(553, b"5.7.60 not allowed to send as", "geo@rf.example"), "发件地址"),
    (smtplib.SMTPAuthenticationError(535, b"5.7.139 Authentication unsuccessful"), "登录验证失败"),
    (OSError("connection refused"), "稍后重试"),
])
async def test_smtp_sender_auth_and_network_errors(email_settings, monkeypatch, exc, expected):
    class Failing(FakeSMTP):
        def send_message(self, message, from_addr=None, to_addrs=None):
            raise exc
    monkeypatch.setattr(smtplib, "SMTP", Failing)
    message = mailer.build_message(email_settings, "a@b.example", "Acme", "https://a.example", 50, b"%PDF")
    with pytest.raises(mailer.EmailError, match=expected):
        await mailer.send_report(email_settings, message)


async def test_port_465_uses_implicit_tls_and_partial_refusal_is_logged(email_settings, monkeypatch, caplog):
    class PartlyRefusing(FakeSMTP):
        def send_message(self, message, from_addr=None, to_addrs=None):
            return {to_addrs[-1]: (550, b"5.1.1 no such user")}
    monkeypatch.setattr(smtplib, "SMTP_SSL", PartlyRefusing)
    settings = email_settings.model_copy(update={"smtp_port": 465, "smtp_security": "starttls"})
    await mailer.send_report(settings, mailer.build_message(settings, "a@b.example", "Acme", "https://a.example", 50, b"%PDF"))
    assert "starttls" not in FakeSMTP.instances[-1].calls and "refused some recipients" in caplog.text


@pytest.mark.parametrize("address,ok", [("a@b.example", True), ("a.b+c@sub.b.example", True), ("a@b", False),
                                         ("a@@b.example", False), ("a@b..example", False), ("a b@c.example", False),
                                         ("x" * 250 + "@b.example", False)])
def test_valid_email(address, ok):
    assert mailer.valid_email(address) is ok


def test_report_requests_are_capped_per_visitor_per_day(email_settings, mock_transport, sent):
    settings = email_settings.model_copy(update={"rate_limit": "100/minute"})
    assert settings.report_rate_limit == "3/day"
    with TestClient(create_app(settings, claude_client=claude_with_analysis(), transport=mock_transport)) as client:
        audit_id = run_audit(client)
        codes = [client.post("/api/report", json={"audit_id": audit_id, "email": "a@b.example"}).status_code
                 for _ in range(4)]
        fourth = client.post("/api/report", json={"audit_id": audit_id, "email": "a@b.example"})
    assert codes[:3] == [200, 200, 200] and codes[3] == 429
    assert fourth.status_code == 429 and "每天3次" in fourth.json()["detail"]


def test_report_with_failed_ai_analysis_is_not_cached(email_settings, mock_transport, sent):
    claude = RoutingClaude(SMART_CLEANING)
    claude.routes.append(("senior GEO (Generative Engine Optimization) consultant", "no json here"))
    with TestClient(create_app(email_settings, claude_client=claude, transport=mock_transport)) as client:
        audit_id = run_audit(client)
        for _ in range(2):
            job = client.post("/api/report", json={"audit_id": audit_id, "email": "a@b.example"}).json()["job_id"]
            assert wait_report(client, job)["status"] == "sent"
        assert client.app.state.reports.cache.get(f"{report_jobs.FULL_KEY}{audit_id}", 3600) is None
    assert claude.count("senior GEO") == 2  # retried, not served from a template-text cache
