"""Saved report data and run history: pre-built reports, reuse across reports of a site, the history log."""

import time

import pytest
from fastapi.testclient import TestClient

from app.cli import print_history
from app.main import create_app
from app.records import RunLog, RunRecord
from tests.integration.test_report_api import claude_with_analysis, run_audit, wait_report

pytestmark = pytest.mark.integration


@pytest.fixture
def email_settings(test_settings):
    return test_settings.model_copy(update={"smtp_host": "smtp.example", "smtp_from": "geo@rf.example"})


@pytest.fixture(autouse=True)
def no_smtp(monkeypatch):
    async def fake_send(settings, message):
        return None

    monkeypatch.setattr("app.report.jobs.send_report", fake_send)


def wait_for_runs(client: TestClient, kind: str, count: int = 1, timeout: float = 20.0) -> list[RunRecord]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = client.app.state.runs.recent(50, kind)
        if len(found) >= count:
            return found
        time.sleep(0.05)
    raise AssertionError(f"no {kind} run recorded")


def test_report_is_prebuilt_after_the_quick_audit_and_send_uses_it(email_settings, mock_transport):
    claude = claude_with_analysis()
    with TestClient(create_app(email_settings, claude_client=claude, transport=mock_transport)) as client:
        audit_id = run_audit(client)
        prewarm = wait_for_runs(client, "prewarm")[0]  # built in the background, nobody asked yet
        assert claude.count("senior GEO") == 1
        job = client.post("/api/report", json={"audit_id": audit_id, "email": "a@b.example"}).json()["job_id"]
        assert wait_report(client, job)["status"] == "sent"
        sent = wait_for_runs(client, "report")[0]
        audit = client.app.state.runs.recent(5, "audit")[0]
    assert claude.count("senior GEO") == 1  # the send reused the pre-built analysis
    assert sent.cached and sent.status == "sent" and sent.domain == "acmeclean.example"
    assert not prewarm.cached and prewarm.brand == "Acme Clean" and prewarm.score is not None
    assert audit.kind == "audit" and not audit.cached


def test_site_analysis_is_shared_by_reports_on_the_same_site(email_settings, mock_transport):
    settings = email_settings.model_copy(update={"report_prewarm": False})
    claude = claude_with_analysis()
    with TestClient(create_app(settings, claude_client=claude, transport=mock_transport)) as client:
        first, second = run_audit(client), run_audit(client)  # second visitor: same saved quick audit
        for audit_id in (first, second):
            job = client.post("/api/report", json={"audit_id": audit_id, "email": "a@b.example"}).json()["job_id"]
            assert wait_report(client, job)["status"] == "sent"
        reports = wait_for_runs(client, "report", 2)
        audits = client.app.state.runs.recent(5, "audit")
    assert claude.count("senior GEO") == 1
    assert [r.cached for r in reversed(reports)] == [False, True]
    assert [a.cached for a in reversed(audits)] == [False, True]  # the second quick audit was the saved copy


def test_prewarm_is_off_without_email_or_when_disabled(test_settings, email_settings, mock_transport):
    for settings in (test_settings, email_settings.model_copy(update={"report_prewarm": False})):
        claude = claude_with_analysis()
        with TestClient(create_app(settings, claude_client=claude, transport=mock_transport)) as client:
            run_audit(client)
            time.sleep(0.3)
        assert claude.count("senior GEO") == 0


def test_run_log_persists_and_lists_newest_first(tmp_path, capsys):
    path = tmp_path / "cache.sqlite3"
    runs = RunLog(path)
    runs.add(RunRecord("audit", "a.example", "A", 50.0, 12.3, False))
    runs.add(RunRecord("report", "a.example", "A", 41.0, 2.0, True, "sent"))
    runs.close()
    reopened = RunLog(path)  # survives a restart (on Railway: the persistent volume)
    assert [r.kind for r in reopened.recent()] == ["report", "audit"]
    assert reopened.recent(kind="audit")[0].seconds == 12.3 and reopened.recent(1)[0].cached is True
    print_history(reopened)
    out = capsys.readouterr().out
    assert "report   sent   saved" in out and "audit    ok     fresh" in out and "a.example" in out
