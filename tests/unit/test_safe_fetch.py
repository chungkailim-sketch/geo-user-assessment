import httpx
import pytest

from app.config import Settings
from app.fetch.safe_fetch import (
    BlockedError,
    PublicOnlyBackend,
    site_root,
    FetchError,
    SafeFetcher,
    UnsafeURLError,
    assert_public_host,
    is_public_ip,
    looks_like_challenge,
    normalize_url,
)

pytestmark = pytest.mark.unit
PUBLIC_IP = "93.184.216.34"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("example.com", "https://example.com/"),
        ("  HTTPS://Example.com/path?q=1#frag ", "https://example.com/path?q=1"),
        ("http://www.example.co.uk", "http://www.example.co.uk/"),
    ],
)
def test_normalize_url_valid(raw, expected):
    assert normalize_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "ftp://example.com", "javascript:alert(1)", "https://localhost", "https://user:pw@example.com",
     "https://example.com:22", "https://example.com:8080", "https://example.com:notaport", "file:///etc/passwd"],
)
def test_normalize_url_rejects(raw):
    with pytest.raises(UnsafeURLError):
        normalize_url(raw)


@pytest.mark.parametrize(
    "address,public",
    [("8.8.8.8", True), ("10.0.0.1", False), ("127.0.0.1", False), ("169.254.169.254", False),
     ("192.168.1.1", False), ("::1", False), ("::ffff:127.0.0.1", False), ("100.64.0.1", False),
     ("2606:4700::1111", True), ("2002:7f00:1::", False), ("64:ff9b::7f00:1", False), ("2001:0:4136::1", False)],
)
def test_is_public_ip(address, public):
    assert is_public_ip(address) is public


async def test_assert_public_host_blocks_private_literal():
    with pytest.raises(UnsafeURLError):
        await assert_public_host("169.254.169.254", Settings(allow_private_hosts=False))


async def test_assert_public_host_blocks_names_resolving_privately(monkeypatch):
    async def fake_getaddrinfo(*args, **kwargs):
        return [(None, None, None, None, ("10.1.2.3", 0))]

    import asyncio

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(UnsafeURLError):
        await assert_public_host("internal.example", Settings(allow_private_hosts=False))


def _fetcher(handler, **overrides) -> SafeFetcher:
    settings = Settings(**{"per_host_delay_seconds": 0, "allow_private_hosts": False, **overrides})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    return SafeFetcher(settings, client=client)


async def test_fetch_follows_safe_redirect():
    def handler(request):
        if request.url.path == "/":
            return httpx.Response(301, headers={"location": "/home"})
        return httpx.Response(200, text="<p>ok</p>", headers={"content-type": "text/html"})

    result = await _fetcher(handler).fetch(f"http://{PUBLIC_IP}/")
    assert result.url.endswith("/home")
    assert result.text == "<p>ok</p>"


async def test_fetch_blocks_redirect_to_metadata_service():
    def handler(request):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    with pytest.raises(UnsafeURLError):
        await _fetcher(handler).fetch(f"http://{PUBLIC_IP}/")


async def test_fetch_limits_redirect_chain():
    def handler(request):
        return httpx.Response(302, headers={"location": "/again"})

    with pytest.raises(FetchError, match="重定向次数过多"):
        await _fetcher(handler, max_redirects=2).fetch(f"http://{PUBLIC_IP}/")


async def test_fetch_rejects_non_html():
    def handler(request):
        return httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})

    with pytest.raises(FetchError, match="内容类型不受支持"):
        await _fetcher(handler).fetch(f"http://{PUBLIC_IP}/")


async def test_fetch_reports_http_errors():
    def handler(request):
        return httpx.Response(503, text="down", headers={"content-type": "text/html"})

    with pytest.raises(FetchError, match="503"):
        await _fetcher(handler).fetch(f"http://{PUBLIC_IP}/")


async def test_fetch_caps_response_size():
    def handler(request):
        return httpx.Response(200, content=b"a" * 5000, headers={"content-type": "text/html"})

    result = await _fetcher(handler, max_response_bytes=1000).fetch(f"http://{PUBLIC_IP}/")
    assert len(result.text) <= 1000


async def test_fetch_wraps_connection_errors():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(FetchError, match="无法连接"):
        await _fetcher(handler).fetch(f"http://{PUBLIC_IP}/")


async def test_fetch_wraps_timeouts():
    def handler(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(FetchError, match="超时"):
        await _fetcher(handler).fetch(f"http://{PUBLIC_IP}/")


@pytest.mark.parametrize("status", [202, 401, 403, 429])
async def test_fetch_reports_bot_protection_status(status):
    def handler(request):
        return httpx.Response(status, text="<html>wait</html>", headers={"content-type": "text/html"})

    with pytest.raises(BlockedError, match="反爬虫防护"):
        await _fetcher(handler).fetch(f"http://{PUBLIC_IP}/")


async def test_fetch_detects_challenge_page_served_with_200():
    def handler(request):
        page = "<html><title>Just a moment...</title><div id='cf-challenge'></div></html>"
        return httpx.Response(200, text=page, headers={"content-type": "text/html"})

    with pytest.raises(BlockedError):
        await _fetcher(handler).fetch(f"http://{PUBLIC_IP}/")


def test_challenge_heuristics_avoid_false_positives():
    assert not looks_like_challenge(200, "<form>Please complete the captcha to subscribe</form>")
    assert not looks_like_challenge(200, "<p>Access denied to members area</p>")
    assert not looks_like_challenge(202, "<p>" + "x" * 30000 + "</p>")  # a real (large) 202 page
    assert looks_like_challenge(200, "<html><title>Access Denied</title></html>")
    assert looks_like_challenge(202, "<html>please wait</html>")



async def test_pinned_backend_refuses_private_targets_at_connect_time(monkeypatch):
    """DNS rebinding: even if the pre-check saw a public IP, connect re-resolves and validates."""
    import asyncio

    async def rebinding_getaddrinfo(*args, **kwargs):
        return [(None, None, None, None, ("127.0.0.1", 0))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", rebinding_getaddrinfo)
    with pytest.raises(UnsafeURLError):
        await PublicOnlyBackend().connect_tcp("rebind.attacker.example", 80)
    with pytest.raises(UnsafeURLError):
        await PublicOnlyBackend().connect_unix_socket("/var/run/docker.sock")


async def test_pinned_backend_connects_to_validated_ip(monkeypatch):
    calls = []

    class Inner:
        async def connect_tcp(self, host, port, *args):
            calls.append((host, port))
            return "stream"

    backend = PublicOnlyBackend()
    backend._inner = Inner()
    assert await backend.connect_tcp(PUBLIC_IP, 443) == "stream"
    assert calls == [(PUBLIC_IP, 443)]


@pytest.mark.parametrize("raw,root", [
    ("https://Nike.com/careers/page?x=1", "https://nike.com/"),
    ("dyson.com", "https://dyson.com/"),
])
def test_site_root(raw, root):
    assert site_root(raw) == root


async def test_politeness_delay_is_per_host_not_global():
    import time as _time

    fetcher = _fetcher(lambda r: httpx.Response(200), per_host_delay_seconds=0.3)
    start = _time.monotonic()
    import asyncio
    await asyncio.gather(fetcher._politeness_wait("a.example"), fetcher._politeness_wait("b.example"))
    assert _time.monotonic() - start < 0.2  # different hosts don't wait on each other
    await fetcher._politeness_wait("a.example")
    assert _time.monotonic() - start >= 0.25  # same host is spaced out


async def test_fetch_enforces_total_deadline():
    import asyncio

    async def slow_handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200, text="<p>late</p>", headers={"content-type": "text/html"})

    settings = Settings(per_host_delay_seconds=0, allow_private_hosts=False, fetch_deadline_seconds=0.1)
    client = httpx.AsyncClient(transport=httpx.MockTransport(slow_handler))
    with pytest.raises(FetchError, match="超时"):
        await SafeFetcher(settings, client=client).fetch(f"http://{PUBLIC_IP}/")
