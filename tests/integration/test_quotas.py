"""Daily abuse cap: 3 fresh runs per site per day."""

import pytest

from tests.integration.test_api import SMART_CLEANING, RoutingClaude, wait_for

pytestmark = pytest.mark.integration


def start(client, url="www.acmeclean.example", refresh=False):
    return client.post("/api/audit", json={"url": url, "refresh": refresh})


def test_each_site_gets_three_fresh_runs_per_day_cached_views_are_free(make_client, test_settings):
    with make_client(RoutingClaude(SMART_CLEANING), test_settings) as client:
        assert wait_for(client, start(client).json()["job_id"])["status"] == "done"  # run 1 (cache miss)
        for _ in range(3):
            assert start(client).status_code == 200  # cached result: not a run
        assert start(client, refresh=True).status_code == 200  # run 2
        assert start(client, refresh=True).status_code == 200  # run 3
        blocked = start(client, refresh=True)
        other_site = start(client, "www.dyson.com")
    assert blocked.status_code == 429 and "该网址今日诊断次数已达上限（每天3次）" in blocked.json()["detail"]
    assert other_site.status_code == 200
