"""She edits config.yml on a phone with no way to run it first, so the
validation messages are part of the product, not an internal detail."""

import pytest

from jobagent import config as config_module
from jobagent.config import ConfigError, QuietHours

MINIMAL = """
enabled: true
schedule:
  run_hours: [7, 12, 16, 18]
email:
  send_on_run_hours: [7, 18]
"""


def _write(tmp_path, text):
    path = tmp_path / "config.yml"
    path.write_text(text, encoding="utf-8")
    return path


class TestDigestHourGuard:
    def test_accepts_digest_hours_that_are_run_hours(self):
        pass  # covered by test_loads_minimal_config

    def test_rejects_a_digest_hour_with_no_run(self, tmp_path):
        # The original plan had runs at 7/12/16/20 and digests at 8 and 18 --
        # so no digest could ever have been sent. This is that bug, caught.
        path = _write(
            tmp_path,
            """
schedule:
  run_hours: [7, 12, 16, 20]
email:
  send_on_run_hours: [8, 18]
""",
        )

        with pytest.raises(ConfigError) as excinfo:
            config_module.load(path)

        message = str(excinfo.value)
        assert "8:00" in message and "18:00" in message
        assert "No email would ever be sent" in message

    def test_error_message_names_the_valid_times(self, tmp_path):
        path = _write(
            tmp_path,
            """
schedule:
  run_hours: [7, 18]
email:
  send_on_run_hours: [9]
""",
        )

        with pytest.raises(ConfigError) as excinfo:
            config_module.load(path)

        assert "7:00, 18:00" in str(excinfo.value)


class TestScoreValidation:
    def test_rejects_push_threshold_below_keep_threshold(self, tmp_path):
        path = _write(
            tmp_path,
            MINIMAL + """
scoring:
  min_score_to_keep: 8
  push_threshold: 6
""",
        )

        with pytest.raises(ConfigError, match="lower than min_score_to_keep"):
            config_module.load(path)

    def test_rejects_out_of_range_scores(self, tmp_path):
        path = _write(tmp_path, MINIMAL + "\nscoring:\n  min_score_to_keep: 50\n")

        with pytest.raises(ConfigError, match="between 0 and 10"):
            config_module.load(path)


class TestHourValidation:
    def test_rejects_12_hour_clock_mistake(self, tmp_path):
        path = _write(tmp_path, "schedule:\n  run_hours: [7, 25]\n")

        with pytest.raises(ConfigError, match="6pm is 18"):
            config_module.load(path)

    def test_rejects_empty_run_hours(self, tmp_path):
        path = _write(tmp_path, "schedule:\n  run_hours: []\n")

        with pytest.raises(ConfigError, match="must be a list of hours"):
            config_module.load(path)


class TestCompanyValidation:
    def test_rejects_company_without_careers_url(self, tmp_path):
        path = _write(
            tmp_path,
            MINIMAL + """
sources:
  companies:
    list:
      - name: Example Ltd
""",
        )

        with pytest.raises(ConfigError, match="careers_url"):
            config_module.load(path)


class TestFriendlyErrorsForTypos:
    """She edits this on a phone. Every mistake must arrive as a sentence, not
    a traceback -- ConfigError subclasses ValueError but not vice versa, so a
    bare int() conversion escaped the friendly handler entirely."""

    def test_a_word_in_a_numeric_field(self, tmp_path):
        path = _write(tmp_path, MINIMAL + "\nemail:\n  max_roles_per_digest: twelve\n")

        with pytest.raises(ConfigError, match="should be a plain number"):
            config_module.load(path)

    def test_a_number_with_units(self, tmp_path):
        path = _write(tmp_path, MINIMAL + '\npush:\n  max_per_day: "3 a day"\n')

        with pytest.raises(ConfigError, match="should be a plain number"):
            config_module.load(path)

    def test_a_twelve_hour_clock_in_quiet_hours(self, tmp_path):
        path = _write(
            tmp_path,
            MINIMAL + '\npush:\n  quiet_hours:\n    start: "9pm"\n    end: "07:30"\n',
        )

        with pytest.raises(ConfigError, match="21:30 rather than 9pm"):
            config_module.load(path)

    def test_an_impossible_time(self, tmp_path):
        path = _write(
            tmp_path,
            MINIMAL + '\npush:\n  quiet_hours:\n    start: "25:00"\n    end: "07:30"\n',
        )

        with pytest.raises(ConfigError, match="isn't a real time"):
            config_module.load(path)

    def test_quiet_hours_are_validated_at_load_not_first_use(self, tmp_path):
        # Otherwise a typo lies dormant until the one evening it matters.
        path = _write(
            tmp_path,
            MINIMAL
            + '\npush:\n  quiet_hours:\n    start: "nonsense"\n    end: "07:30"\n',
        )

        with pytest.raises(ConfigError):
            config_module.load(path)


class TestBlankTemplateEntries:
    """The shipped templates have bare '-' placeholders she hasn't filled in.

    YAML parses those as None, and `str(None)` is the truthy string "None" --
    which would have the agent searching job sites for roles called "None".
    """

    def test_blank_search_terms_are_dropped(self, tmp_path):
        path = _write(
            tmp_path,
            MINIMAL + """
sources:
  search_terms:
    -
    -
    - operations associate
""",
        )

        cfg = config_module.load(path)

        assert cfg.search_terms == ("operations associate",)

    def test_all_blank_search_terms_give_an_empty_tuple(self, tmp_path):
        path = _write(tmp_path, MINIMAL + "\nsources:\n  search_terms:\n    -\n    -\n")

        assert config_module.load(path).search_terms == ()

    def test_blank_company_placeholders_do_not_raise(self, tmp_path):
        path = _write(
            tmp_path,
            MINIMAL + """
sources:
  companies:
    list:
      -
""",
        )

        assert config_module.load(path).companies == ()

    def test_blank_locations_fall_back_to_nothing_not_none(self, tmp_path):
        path = _write(tmp_path, MINIMAL + "\nsources:\n  locations:\n    -\n")

        assert config_module.load(path).locations == ()


class TestDefaults:
    def test_loads_minimal_config_with_sensible_defaults(self, tmp_path):
        cfg = config_module.load(_write(tmp_path, MINIMAL))

        assert cfg.enabled is True
        assert cfg.run_hours == (7, 12, 16, 18)
        assert cfg.email_digest_hours == (7, 18)
        assert cfg.push_threshold == 8
        assert cfg.min_score_to_keep == 6
        assert cfg.seen_retention_days == 60

    def test_missing_sections_do_not_crash(self, tmp_path):
        # Adding a setting later must not break a config she hasn't updated.
        cfg = config_module.load(_write(tmp_path, "enabled: false\n"))

        assert cfg.enabled is False
        assert cfg.run_hours == (7, 12, 16, 18)

    def test_empty_file(self, tmp_path):
        cfg = config_module.load(_write(tmp_path, ""))

        assert cfg.enabled is True


class TestQuietHours:
    def test_window_wrapping_past_midnight(self):
        # The normal case here, and the one a naive comparison gets wrong.
        quiet = QuietHours(start="21:30", end="07:30")

        assert quiet.covers(22) is True
        assert quiet.covers(3) is True
        assert quiet.covers(7) is True
        assert quiet.covers(12) is False
        assert quiet.covers(18) is False

    def test_window_within_one_day(self):
        quiet = QuietHours(start="09:00", end="17:00")

        assert quiet.covers(12) is True
        assert quiet.covers(20) is False
