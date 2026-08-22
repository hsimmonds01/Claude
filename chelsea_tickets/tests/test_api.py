"""Tests for the feed client, with HTTP mocked out."""

from __future__ import annotations

import pytest
import requests

from chelsea import api


class FakeResponse:
    def __init__(self, text: str = "", payload=None, status: int = 200):
        self.text = text
        self._payload = payload
        self.status_code = status

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


class TestResolvePageId:
    def test_reads_the_page_id_out_of_escaped_page_html(self, monkeypatch):
        # Arrange -- the real page embeds its JSON inside an HTML attribute,
        # so the quotes arrive as &quot; and a naive regex finds nothing.
        html_page = 'data-props="{&quot;pageId&quot;:&quot;realId123456789&quot;}"'
        monkeypatch.setattr(api, "_get", lambda url, params=None: FakeResponse(text=html_page))

        # Act / Assert
        assert api.resolve_page_id() == "realId123456789"

    def test_falls_back_when_the_page_cannot_be_fetched(self, monkeypatch):
        # Arrange
        def boom(url, params=None):
            raise api.FetchError("network down")

        monkeypatch.setattr(api, "_get", boom)

        # Act / Assert -- a missing page must not stop the run entirely
        assert api.resolve_page_id() == api.FALLBACK_PAGE_ID

    def test_falls_back_when_the_page_no_longer_exposes_a_page_id(self, monkeypatch):
        monkeypatch.setattr(api, "_get", lambda url, params=None: FakeResponse(text="<html/>"))
        assert api.resolve_page_id() == api.FALLBACK_PAGE_ID


class TestFetchTicketFeed:
    def test_requests_a_page_size_large_enough_for_a_full_fixture_list(self, monkeypatch):
        # Arrange -- the API defaults to 6 items, which would silently drop
        # fixtures once Chelsea lists more than six at once.
        seen = {}

        def fake_get(url, params=None):
            seen["url"] = url
            seen["params"] = params
            return FakeResponse(payload={"items": []})

        monkeypatch.setattr(api, "_get", fake_get)

        # Act
        api.fetch_ticket_feed(page_id="abc")

        # Assert
        assert seen["url"] == api.TICKETS_API_URL
        assert seen["params"]["pageId"] == "abc"
        assert seen["params"]["pageSize"] >= 100

    def test_resolves_the_page_id_when_none_is_supplied(self, monkeypatch):
        # Arrange
        monkeypatch.setattr(api, "resolve_page_id", lambda: "resolved99")
        captured = {}

        def fake_get(url, params=None):
            captured.update(params or {})
            return FakeResponse(payload={"items": []})

        monkeypatch.setattr(api, "_get", fake_get)

        # Act
        api.fetch_ticket_feed()

        # Assert
        assert captured["pageId"] == "resolved99"

    def test_non_json_response_raises_fetch_error(self, monkeypatch):
        # Arrange -- Chelsea's edge serves an HTML error page to blocked bots
        monkeypatch.setattr(api, "_get", lambda url, params=None: FakeResponse(text="<html/>"))

        # Act / Assert
        with pytest.raises(api.FetchError):
            api.fetch_ticket_feed(page_id="abc")

    def test_json_that_is_not_an_object_raises_fetch_error(self, monkeypatch):
        monkeypatch.setattr(api, "_get", lambda url, params=None: FakeResponse(payload=[1, 2]))
        with pytest.raises(api.FetchError):
            api.fetch_ticket_feed(page_id="abc")


class TestRetries:
    def test_a_transient_failure_is_retried_before_giving_up(self, monkeypatch):
        # Arrange
        calls = {"n": 0}

        def flaky(url, params=None, headers=None, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise requests.ConnectionError("blip")
            return FakeResponse(payload={"items": []})

        monkeypatch.setattr(api.requests, "get", flaky)

        # Act
        api._get(api.TICKETS_API_URL)

        # Assert
        assert calls["n"] == 2

    def test_persistent_failure_raises_fetch_error(self, monkeypatch):
        # Arrange
        def always_fail(url, params=None, headers=None, timeout=None):
            raise requests.ConnectionError("down")

        monkeypatch.setattr(api.requests, "get", always_fail)

        # Act / Assert
        with pytest.raises(api.FetchError):
            api._get(api.TICKETS_API_URL)

    def test_sends_browser_headers(self, monkeypatch):
        # Arrange -- Chelsea's edge 404s obvious bots
        captured = {}

        def fake_get(url, params=None, headers=None, timeout=None):
            captured.update(headers or {})
            return FakeResponse(payload={})

        monkeypatch.setattr(api.requests, "get", fake_get)

        # Act
        api._get(api.TICKETS_PAGE_URL)

        # Assert
        assert "Mozilla" in captured["User-Agent"]
