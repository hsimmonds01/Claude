"""Dedupe is the thing most likely to embarrass us in front of her, so it gets
the heaviest tests: the failure mode is a first digest full of triplicates."""

from jobagent.models import Job, merge, normalise_company, normalise_title


def _job(source, title, company, url, **kwargs):
    return Job(source=source, title=title, company=company, url=url, **kwargs)


class TestNormaliseCompany:
    def test_ignores_legal_suffixes(self):
        assert normalise_company("Example Ltd") == normalise_company("Example Limited")
        assert normalise_company("Example PLC") == normalise_company("Example")

    def test_ignores_case_and_punctuation(self):
        assert normalise_company("St. Mungo's") == normalise_company("St Mungos")

    def test_keeps_genuinely_different_companies_apart(self):
        assert normalise_company("Oxfam") != normalise_company("Oxford University")


class TestNormaliseTitle:
    def test_strips_bracketed_asides(self):
        assert normalise_title("Operations Manager (Hybrid)") == normalise_title(
            "Operations Manager"
        )
        assert normalise_title("Operations Manager [12m FTC]") == normalise_title(
            "Operations Manager"
        )

    def test_strips_agency_reference_numbers(self):
        assert normalise_title("Operations Associate REF 4471") == normalise_title(
            "Operations Associate"
        )

    def test_strips_salary_stuffed_into_title(self):
        assert normalise_title("Operations Associate £35,000") == normalise_title(
            "Operations Associate"
        )

    def test_strips_location_suffix_after_dash(self):
        assert normalise_title("Operations Associate - London") == normalise_title(
            "Operations Associate"
        )

    def test_preserves_seniority(self):
        # The important negative case. Merging these would silently hide one
        # genuinely different job behind another.
        assert normalise_title("Operations Manager") != normalise_title(
            "Senior Operations Manager"
        )
        assert normalise_title("Head of Operations") != normalise_title("Operations")


class TestMerge:
    def test_same_role_from_three_sources_becomes_one(self):
        # Arrange -- the exact scenario that would otherwise fill her first
        # digest with the same job three times.
        jobs = [
            _job("adzuna", "Operations Associate", "Example Ltd", "https://adz/1"),
            _job("reed", "Operations Associate (Hybrid)", "Example Limited", "https://reed/2"),
            _job("inbox", "Operations Associate - London", "Example", "https://li/3"),
        ]

        # Act
        merged = merge(jobs)

        # Assert
        assert len(merged) == 1
        assert set(merged[0].sources) == {"adzuna", "reed", "inbox"}
        assert len(merged[0].all_urls) == 3

    def test_prefers_company_careers_page_as_the_link(self):
        jobs = [
            _job("reed", "Operations Associate", "Example", "https://reed/2"),
            _job("company", "Operations Associate", "Example", "https://example.com/jobs/1"),
        ]

        merged = merge(jobs)

        assert merged[0].url == "https://example.com/jobs/1"

    def test_prefers_the_richest_description_within_one_source(self):
        # An alert email gives a bare title; the AI scores far better with the
        # full text, so the fullest version must be the representative.
        jobs = [
            _job("inbox", "Operations Associate", "Example", "https://a", description=""),
            _job(
                "inbox",
                "Operations Associate",
                "Example",
                "https://b",
                description="Long description of the role " * 20,
            ),
        ]

        merged = merge(jobs)

        assert merged[0].best.url == "https://b"

    def test_different_seniority_stays_separate(self):
        jobs = [
            _job("adzuna", "Operations Manager", "Example", "https://a"),
            _job("adzuna", "Senior Operations Manager", "Example", "https://b"),
        ]

        assert len(merge(jobs)) == 2

    def test_same_title_at_different_companies_stays_separate(self):
        jobs = [
            _job("adzuna", "Operations Associate", "Oxfam", "https://a"),
            _job("adzuna", "Operations Associate", "Shelter", "https://b"),
        ]

        assert len(merge(jobs)) == 2

    def test_drops_entries_too_thin_to_score(self):
        jobs = [
            _job("adzuna", "", "Example", "https://a"),
            _job("adzuna", "Operations Associate", "", "https://b"),
            _job("adzuna", "Operations Associate", "Example", ""),
            _job("adzuna", "Operations Associate", "Example", "https://ok"),
        ]

        merged = merge(jobs)

        assert len(merged) == 1
        assert merged[0].url == "https://ok"

    def test_collects_every_location_seen(self):
        jobs = [
            _job("adzuna", "Operations Associate", "Example", "https://a", location="London"),
            _job("reed", "Operations Associate", "Example", "https://b", location="City of London"),
        ]

        merged = merge(jobs)

        assert set(merged[0].locations) == {"London", "City of London"}

    def test_empty_input(self):
        assert merge([]) == []
