"""Browser test of the full user flow against a local server with a mocked fixture site.

Run with:  pytest -m e2e   (requires `python -m playwright install chromium`)
"""

import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from app.main import create_app
from tests.integration.test_api import RoutingClaude
from tests.unit.test_claude_strategy import BANKING

pytestmark = pytest.mark.e2e
playwright = pytest.importorskip("playwright.sync_api")
SCREENSHOTS = Path(__file__).resolve().parents[2] / "test-results"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def live_server(test_settings, mock_transport):
    claude = RoutingClaude({"industry": "Smart Cleaning"}, {"industry": "other", "custom": BANKING})
    app = create_app(test_settings, claude_client=claude, transport=mock_transport)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.mark.parametrize("viewport", [{"width": 1280, "height": 900}, {"width": 390, "height": 844}])
def test_assessment_flow(live_server, viewport):
    SCREENSHOTS.mkdir(exist_ok=True)
    tag = "desktop" if viewport["width"] > 800 else "mobile"
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=viewport)
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("console", lambda msg: msg.type == "error" and errors.append(msg.text))

        page.goto(live_server + "/")
        page.screenshot(path=str(SCREENSHOTS / f"index-{tag}.png"), full_page=True)
        assert page.input_value("#url") == "https://www..com"
        page.click("#url")
        page.keyboard.type("acme")  # caret lands between "www." and ".com"
        assert page.input_value("#url") == "https://www.acme.com"
        page.fill("#url", "www.acmeclean.example")
        page.click("#submit")

        page.wait_for_url("**/results/*")
        page.wait_for_selector("#report:not([hidden])", timeout=20000)
        assert page.text_content("#brand-name") == "Acme Clean"
        assert page.input_value("#industry-select") == "Smart Cleaning"
        assert "戴森" in page.text_content("#benchmark-title")
        assert page.text_content("#score-label") in ("优秀", "良好", "中等", "偏低", "亟需优化")
        for canvas in ("gauge", "cit-radar", "grade-bar", "platform-bar", "bench-bar", "bench-radar"):
            assert page.eval_on_selector(f"#{canvas}", "c => c.width > 0 && c.height > 0"), canvas
        assert page.locator("#actions > li").count() == 6
        assert "公关与媒体" in page.text_content("#actions li.strategy")
        assert page.locator("#platforms .platform").count() == 5
        no_overflow = page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        assert no_overflow, "horizontal page scroll"
        page.wait_for_timeout(1000)  # let chart animations settle before the screenshot
        page.screenshot(path=str(SCREENSHOTS / f"results-{tag}.png"), full_page=True)

        # Glossary panel: an info button opens it at its entry; Esc closes it and restores focus.
        assert page.locator("#glossary").is_hidden()
        page.click("[data-glossary='term-authority']")
        page.locator("#glossary.open").wait_for()
        assert page.locator("#term-authority").is_visible()
        assert page.evaluate("document.activeElement.id") == "glossary-close"
        page.keyboard.press("Escape")
        page.locator("#glossary").wait_for(state="hidden")
        assert page.evaluate("document.activeElement.dataset.glossary") == "term-authority"
        page.click(".glossary-toggle")
        page.locator("#glossary.open").wait_for()
        page.click("#glossary-backdrop", position={"x": 5, "y": 5})
        page.locator("#glossary").wait_for(state="hidden")

        # Tailored strategy: the most AI-cited media for the industry are listed under 核心策略建议.
        assert page.locator("#actions li.strategy .media-list li").count() == 3
        assert "什么值得买" in page.text_content("#actions li.strategy")

        # "其他": type an industry outside the catalogue; AI finds its leader and the report updates.
        assert page.locator("#custom-industry-form").is_hidden()
        page.select_option("#industry-select", "__other__")
        assert page.locator("#custom-industry-form").is_visible()
        page.fill("#custom-industry", "银行")
        page.click("#custom-industry-submit")
        page.locator("#benchmark-title", has_text="摩根大通").wait_for(timeout=20000)
        assert page.input_value("#industry-select") == "Banking"
        assert page.text_content("#industry-method") == "手动输入"
        assert "银行金融" in page.text_content("#actions li.strategy .media-title")

        page.select_option("#industry-select", "F&B")
        # A selector wait, not wait_for_function: the app's CSP (correctly) forbids string eval.
        page.locator("#benchmark-title", has_text="星巴克").wait_for(timeout=20000)
        assert "暂时无法评估星巴克" in page.text_content("#benchmark-status")
        browser.close()
    assert not errors, errors
