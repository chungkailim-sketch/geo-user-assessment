import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import AUTHORITY_PAYLOAD, FakeClaude, claude_text_response

pytestmark = pytest.mark.integration


def wait_for(client: TestClient, job_id: str, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/audit/{job_id}").json()
        if body["status"] in ("done", "error"):
            return body
        time.sleep(0.05)
    raise AssertionError("audit did not finish")


@pytest.fixture
def make_client(test_settings, mock_transport):
    def factory(claude=None, settings=None):
        app = create_app(settings or test_settings, claude_client=claude, transport=mock_transport)
        return TestClient(app)

    return factory


def test_full_audit_flow_with_claude(make_client):
    claude = FakeClaude([
        claude_text_response(AUTHORITY_PAYLOAD),  # brand authority
        claude_text_response({"industry": "Smart Cleaning"}),  # brand industry
        claude_text_response(AUTHORITY_PAYLOAD),  # leader authority (leader domain: no industry call)
    ])
    with make_client(claude) as client:
        start = client.post("/api/audit", json={"url": "www.acmeclean.example"})
        assert start.status_code == 200
        job = wait_for(client, start.json()["job_id"])

    assert job["status"] == "done", job
    result = job["result"]
    assert result["brand_name"] == "Acme Clean"
    assert result["domain"] == "acmeclean.example"
    assert result["score"] == round(result["citability"]["score"] * 0.55 + result["authority"]["score"] * 0.45, 1)
    assert result["authority"]["confidence"] == "full"
    assert result["industry"]["detected"] == "Smart Cleaning"
    assert result["industry"]["selected"] == "Smart Cleaning"
    assert result["industry"]["method"] == "ai classification"
    assert not any("/faq" in url for url in result["crawl"]["pages"])  # blocked by robots.txt
    benchmark = result["benchmark"]
    assert benchmark["leader"] == "Dyson" and benchmark["status"] == "ok"
    assert benchmark["gaps"]["overall"] == round(result["score"] - benchmark["leader_scores"]["score"], 1)


def test_audit_without_claude_uses_limited_authority_and_cache(make_client):
    with make_client() as client:
        first = wait_for(client, client.post("/api/audit", json={"url": "https://www.acmeclean.example"}).json()["job_id"])
        second = wait_for(client, client.post("/api/audit", json={"url": "https://www.acmeclean.example"}).json()["job_id"])
    assert first["result"]["authority"]["confidence"] == "limited"
    assert first["result"]["audited_at"] == second["result"]["audited_at"]  # served from cache


def test_explicit_industry_and_rebenchmark(make_client):
    with make_client() as client:
        job = wait_for(client, client.post(
            "/api/audit", json={"url": "https://www.acmeclean.example", "industry": "Smart Cleaning"}).json()["job_id"])
        assert job["result"]["industry"]["selected"] == "Smart Cleaning"

        # Switch to F&B: Starbucks host is unknown to the mock, so the leader audit fails gracefully.
        response = client.post("/api/benchmark", json={"audit_id": job["job_id"], "industry": "F&B"})
        assert response.status_code == 200
        assert response.json()["leader"] == "Starbucks"
        assert response.json()["status"] == "unavailable"
        refreshed = client.get(f"/api/audit/{job['job_id']}").json()
        assert refreshed["result"]["industry"]["selected"] == "F&B"


def test_industry_outside_catalogue_is_identified_and_benchmarked(make_client):
    custom = {"name": "Home Robotics", "name_zh": "家用机器人", "leader": "iRobot",
              "leader_zh": "iRobot", "leader_url": "https://www.irobot.example"}
    claude = FakeClaude([
        claude_text_response(AUTHORITY_PAYLOAD),
        claude_text_response({"industry": "other", "custom": custom}),
    ])
    with make_client(claude) as client:
        job = wait_for(client, client.post("/api/audit", json={"url": "www.acmeclean.example"}).json()["job_id"])
        industry = job["result"]["industry"]
        assert industry["method"] == "ai custom" and industry["selected"] == "Home Robotics"
        assert industry["options"][0] == "Home Robotics" and len(industry["options"]) == 8
        assert industry["option_labels"]["Home Robotics"] == "家用机器人"
        benchmark = job["result"]["benchmark"]
        assert benchmark["leader"] == "iRobot" and benchmark["custom"] is True
        assert benchmark["status"] == "unavailable"  # the mock site doesn't serve irobot.example

        # Switching to a catalogue industry and back to the AI-identified one both work.
        assert client.post("/api/benchmark", json={"audit_id": job["job_id"], "industry": "F&B"}).json()["leader"] == "Starbucks"
        back = client.post("/api/benchmark", json={"audit_id": job["job_id"], "industry": "Home Robotics"})
        assert back.status_code == 200 and back.json()["leader_zh"] == "iRobot"
        unknown = client.post("/api/benchmark", json={"audit_id": job["job_id"], "industry": "Banking"})
        assert unknown.status_code == 404


def test_leader_site_is_its_own_benchmark(make_client):
    with make_client() as client:
        job = wait_for(client, client.post("/api/audit", json={"url": "https://www.dyson.com"}).json()["job_id"])
    assert job["result"]["benchmark"]["is_leader"] is True
    assert job["result"]["industry"]["method"] == "leader domain"


def test_unreachable_site_reports_friendly_error(make_client):
    with make_client() as client:
        job = wait_for(client, client.post("/api/audit", json={"url": "https://nowhere.example"}).json()["job_id"])
    assert job["status"] == "error"
    assert "404" in job["error"]


@pytest.mark.parametrize("payload,status", [
    ({"url": "ftp://x.example"}, 400),
    ({"url": "localhost"}, 400),
    ({"url": "https://ok.example", "industry": "Banking"}, 400),
    ({"url": ""}, 422),
    ({}, 422),
])
def test_start_audit_validation(make_client, payload, status):
    with make_client() as client:
        assert client.post("/api/audit", json=payload).status_code == status


def test_unknown_and_malformed_job_ids(make_client):
    with make_client() as client:
        assert client.get("/api/audit/" + "0" * 32).status_code == 404
        assert client.get("/api/audit/../../etc").status_code == 404
        assert client.get("/results/not-a-job").status_code == 404
        assert client.post("/api/benchmark", json={"audit_id": "0" * 32, "industry": "F&B"}).status_code == 404
        assert client.post("/api/benchmark", json={"audit_id": "bad", "industry": "F&B"}).status_code == 422


def test_pages_render_with_security_headers(make_client):
    with make_client() as client:
        index = client.get("/")
        assert index.status_code == 200
        assert 'lang="zh-CN"' in index.text
        assert "CBGA GEO Readiness Assessment" in index.text and "/static/img/rf-geo-logo.png" in index.text
        assert 'value="https://www..com"' in index.text
        assert "Citability × 0.55" not in index.text and "可引用性 × 0.55" not in index.text
        assert "explainer" not in index.text  # the three info boxes were removed
        assert "罗德亚洲AI可见度与GEO优化咨询。欲了解更多，请点击" in index.text
        assert 'href="https://www.ruderfinnasia.com/zh/products/ai-visibility-geo-advisory"' in index.text
        assert client.get("/static/img/rf-geo-logo.png").status_code == 200
        assert 'rel="icon" type="image/svg+xml" href="/static/img/rf-favicon.svg' in index.text
        assert client.get("/static/img/rf-favicon.svg").status_code == 200
        # Glossary panel: score ranges, every catalogue category with its leader, and term definitions
        assert 'id="glossary"' in index.text and "/static/js/glossary.js" in index.text
        assert all(label in index.text for label in ("分数区间", "行业类别", "术语释义", "亟需优化", "其他行业（AI识别）"))
        assert "智能清洁" in index.text and "标杆：戴森（Dyson）" in index.text
        assert 'id="term-citability"' in index.text and 'id="term-authority"' in index.text
        assert "default-src 'self'" in index.headers["content-security-policy"]
        assert index.headers["x-content-type-options"] == "nosniff"
        results = client.get("/results/" + "a" * 32)
        assert results.status_code == 200 and 'data-job-id="' + "a" * 32 in results.text
        assert client.get("/healthz").json() == {"ok": True}
        assert "object-src 'none'" in index.headers["content-security-policy"]
        assert "max-age" in index.headers["strict-transport-security"]
        assert len(client.get("/api/industries").json()) == 7


def test_rate_limit(make_client, test_settings):
    limited = test_settings.model_copy(update={"rate_limit": "2/minute"})
    with make_client(settings=limited) as client:
        codes = [client.post("/api/audit", json={"url": "https://www.acmeclean.example"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_rate_limit_behind_proxy_keys_on_rightmost_forwarded_ip(make_client, test_settings):
    proxied = test_settings.model_copy(update={"rate_limit": "1/minute", "trust_proxy_headers": True})
    body = {"url": "https://www.acmeclean.example"}
    with make_client(settings=proxied) as client:
        first = client.post("/api/audit", json=body, headers={"X-Forwarded-For": "6.6.6.6, 1.1.1.1"})
        # Spoofing the left-hand entry doesn't reset the limit; the proxy-appended IP is what counts.
        spoofed = client.post("/api/audit", json=body, headers={"X-Forwarded-For": "9.9.9.9, 1.1.1.1"})
        other_client = client.post("/api/audit", json=body, headers={"X-Forwarded-For": "2.2.2.2"})
    assert [first.status_code, spoofed.status_code, other_client.status_code] == [200, 429, 200]


def test_subpage_audit_uses_homepage_so_baselines_cannot_be_poisoned(make_client):
    with make_client() as client:
        job = wait_for(client, client.post("/api/audit", json={"url": "https://www.acmeclean.example/about"}).json()["job_id"])
    assert job["result"]["url"] == "https://www.acmeclean.example/"
    assert job["result"]["crawl"]["pages"][0] == "https://www.acmeclean.example/"


def test_refresh_is_throttled(make_client):
    with make_client() as client:
        first = wait_for(client, client.post("/api/audit", json={"url": "www.acmeclean.example"}).json()["job_id"])
        again = wait_for(client, client.post(
            "/api/audit", json={"url": "www.acmeclean.example", "refresh": True}).json()["job_id"])
    assert first["result"]["audited_at"] == again["result"]["audited_at"]


def test_degraded_results_are_not_cached(make_client):
    claude = FakeClaude([RuntimeError("anthropic down")] * 6)
    with make_client(claude) as client:
        first = wait_for(client, client.post("/api/audit", json={"url": "www.acmeclean.example"}).json()["job_id"])
        calls_after_first = len(claude.calls)
        wait_for(client, client.post("/api/audit", json={"url": "www.acmeclean.example"}).json()["job_id"])
    assert first["result"]["degraded"] is True
    assert first["result"]["authority"]["confidence"] == "limited"
    assert len(claude.calls) > calls_after_first  # re-ran instead of serving the degraded cache


def test_queue_full_returns_503(make_client, test_settings):
    tiny = test_settings.model_copy(update={"max_queued_jobs": 0})
    with make_client(settings=tiny) as client:
        response = client.post("/api/audit", json={"url": "www.acmeclean.example"})
    assert response.status_code == 503
    assert response.headers["retry-after"] == "60"
