"""Timezone handling, with the clock NOT stubbed.

The bug these exist for: `datetime.now()` on a GitHub runner is UTC, while
every hour in config.yml is labelled UK time and cron-job.org is configured
in Europe/London. Through British Summer Time the 07:00 London trigger
arrived as hour 6, matched nothing in run_hours, and the run exited having
done nothing — successfully, so no failure email either.

What made it survive a full test suite is the same blind spot as the
sender-allowlist bypass: every scheduling test replaced the clock wholesale,
so the conversion between the runner's time and hers was never exercised.
These tests deliberately use real timezone data and real instants.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from jobagent import config as config_module
from jobagent.config import ConfigError

BASE = """
schedule:
  run_hours: [7, 12, 16, 18]
  timezone: Europe/London
email:
  send_on_run_hours: [7, 18]
"""


def _write(tmp_path, text):
    path = tmp_path / "config.yml"
    path.write_text(text, encoding="utf-8")
    return path


class TestTheLiveBug:
    def test_an_0600_utc_instant_in_july_is_hour_7_for_her(self, tmp_path):
        """The exact failure. cron-job.org fires the '7am' London trigger at
        06:00 UTC in summer; the runner's clock calls that hour 6, which is in
        no one's run_hours, so the agent no-opped four times a day."""
        cfg = config_module.load(_write(tmp_path, BASE))
        summer_instant = datetime(2026, 7, 27, 6, 0, tzinfo=UTC)

        local = summer_instant.astimezone(ZoneInfo(cfg.timezone))

        assert local.hour == 7
        assert local.hour in cfg.run_hours

    def test_the_same_wall_clock_hour_in_winter_is_0700_utc(self, tmp_path):
        # GMT: no offset, so the UTC hour and hers agree. The bug was
        # invisible in winter, which is its own hazard.
        cfg = config_module.load(_write(tmp_path, BASE))
        winter_instant = datetime(2026, 1, 27, 7, 0, tzinfo=UTC)

        local = winter_instant.astimezone(ZoneInfo(cfg.timezone))

        assert local.hour == 7

    def test_config_now_is_timezone_aware_and_uses_her_zone(self, tmp_path):
        # Not stubbed: reads the real current time.
        cfg = config_module.load(_write(tmp_path, BASE))

        now = cfg.now()

        assert now.tzinfo is not None, "a naive datetime is the bug itself"
        assert str(now.tzinfo) == "Europe/London"

    def test_her_hour_can_differ_from_the_runners(self, tmp_path):
        """Proves the two clocks are genuinely different sources.

        In BST these differ; in GMT they agree. Asserting only the conversion
        is correct keeps this meaningful year-round.
        """
        cfg = config_module.load(_write(tmp_path, BASE))

        hers = cfg.now()
        utc_equivalent = hers.astimezone(UTC)

        assert hers.hour == (utc_equivalent + (hers.utcoffset() or 0)).hour


class TestTimezoneValidation:
    def test_a_typo_gets_a_readable_error(self, tmp_path):
        path = _write(tmp_path, "schedule:\n  timezone: Europe/Londn\n")

        with pytest.raises(ConfigError, match="isn't a timezone name"):
            config_module.load(path)

    def test_the_error_names_the_right_answer(self, tmp_path):
        path = _write(tmp_path, "schedule:\n  timezone: GMT+1\n")

        with pytest.raises(ConfigError, match="Europe/London"):
            config_module.load(path)

    def test_a_valid_alternative_zone_is_accepted(self, tmp_path):
        cfg = config_module.load(
            _write(tmp_path, "schedule:\n  timezone: Europe/Paris\n")
        )

        assert cfg.timezone == "Europe/Paris"
        assert str(cfg.now().tzinfo) == "Europe/Paris"

    def test_the_default_is_london(self, tmp_path):
        assert config_module.load(_write(tmp_path, "enabled: true\n")).timezone == (
            "Europe/London"
        )


class TestBooleanHours:
    def test_a_yaml_boolean_is_not_accepted_as_an_hour(self, tmp_path):
        # Bare `yes`/`on`/`true` are YAML booleans, and bool subclasses int,
        # so `- yes` silently became hour 1.
        path = _write(tmp_path, "schedule:\n  run_hours: [yes, 12]\n")

        with pytest.raises(ConfigError, match="Hours must be whole numbers"):
            config_module.load(path)

    def test_a_genuine_hour_zero_still_works(self, tmp_path):
        # False would also be rejected; 0 must not be.
        cfg = config_module.load(
            _write(
                tmp_path,
                "schedule:\n  run_hours: [0, 12]\nemail:\n  send_on_run_hours: [12]\n",
            )
        )

        assert 0 in cfg.run_hours
