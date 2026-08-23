"""Tests for the ntfy notification layer."""

from __future__ import annotations

import importlib

import pytest
import requests

from chelsea import notify


class FakeResponse:
    def __init__(self, status: int = 200):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


@pytest.fixture
def captured(monkeypatch):
    seen = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        seen.update(url=url, data=data, headers=headers)
        return FakeResponse()

    monkeypatch.setattr(notify.requests, "post", fake_post)
    return seen


class TestSend:
    def test_title_priority_and_tags_go_in_the_headers(self, captured):
        # Act
        notify.send("Ballot open", "Body text", priority="urgent", tags="soccer")

        # Assert
        assert captured["headers"]["Title"] == "Ballot open"
        assert captured["headers"]["Priority"] == "urgent"
        assert captured["headers"]["Tags"] == "soccer"

    def test_body_is_sent_as_utf8_bytes(self, captured):
        # Arrange -- opponent names carry accents (e.g. Atlético)
        notify.send("t", "Chelsea v Atlético")

        # Assert
        assert captured["data"] == "Chelsea v Atlético".encode()

    def test_a_failed_post_raises_so_the_run_fails_loudly(self, monkeypatch):
        # Arrange -- a silently dropped alert is the worst failure mode here
        monkeypatch.setattr(
            notify.requests, "post",
            lambda *a, **k: FakeResponse(status=500),
        )

        # Act / Assert
        with pytest.raises(requests.HTTPError):
            notify.send("t", "m")

    def test_the_topic_is_never_printed(self, capsys, captured):
        # Arrange -- this repo is public, so Actions logs are public too
        notify.send("t", "m")

        # Assert
        assert notify.NTFY_TOPIC not in capsys.readouterr().out


class TestTopicResolution:
    def test_an_empty_env_var_falls_back_to_the_default_topic(self, monkeypatch):
        # Arrange -- GitHub Actions sets a MISSING secret to "" rather than
        # leaving it unset, so a `.get(name, default)` would yield "" and
        # every notification would post into a nonexistent topic.
        monkeypatch.setenv("NTFY_TOPIC", "")

        # Act
        reloaded = importlib.reload(notify)

        # Assert
        try:
            assert reloaded.NTFY_TOPIC == reloaded.DEFAULT_NTFY_TOPIC
        finally:
            monkeypatch.delenv("NTFY_TOPIC", raising=False)
            importlib.reload(notify)

    def test_a_configured_topic_is_used(self, monkeypatch):
        # Arrange
        monkeypatch.setenv("NTFY_TOPIC", "some-other-topic")

        # Act
        reloaded = importlib.reload(notify)

        # Assert
        try:
            assert reloaded.NTFY_URL.endswith("some-other-topic")
        finally:
            monkeypatch.delenv("NTFY_TOPIC", raising=False)
            importlib.reload(notify)


class TestTestNotification:
    def test_test_alert_is_clearly_labelled(self, captured):
        # Act
        notify.send_test()

        # Assert
        assert captured["headers"]["Title"].startswith("TEST:")
