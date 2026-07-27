"""Alert-email parsing, built against synthetic emails.

No real mailbox existed when this was written, so these fixtures imitate the
documented shape of each provider's alerts rather than being recorded from
real ones. Re-check against genuine emails once the mailbox is live -- the
symptom of a layout change is a provider quietly yielding zero jobs.
"""

from jobagent.sources import inbox

LINKEDIN_EMAIL = """
<html><body>
  <p>Your job alert for operations associate</p>
  <a href="https://www.linkedin.com/comm/jobs/view/3812345678/?trk=eml-jobs_alert">
    Operations Associate
  </a>
  <div>Riverbank Trust · London, England (Hybrid)</div>
  <a href="https://www.linkedin.com/comm/jobs/view/3899999999/?trk=eml-jobs_alert">
    Operations Coordinator
  </a>
  <div>Halcyon Group · London, England</div>
  <a href="https://www.linkedin.com/comm/jobs/search/?trk=eml-footer">See all jobs</a>
  <a href="https://www.linkedin.com/psettings/email">Unsubscribe</a>
</body></html>
"""

WTTJ_EMAIL = """
<html><body>
  <a href="https://www.welcometothejungle.com/en/companies/acme/jobs/operations-manager">
    Operations Manager
  </a>
  <div>Acme Studio · Paris</div>
</body></html>
"""

INDEED_EMAIL = """
<html><body>
  <a href="https://t.indeed.com/r?url=https%3A%2F%2Fuk.indeed.com%2Fviewjob%3Fjk%3Dabc123&c=track">
    Operations Executive
  </a>
  <div>Southwark Arts · London</div>
</body></html>
"""


class TestRedirectUnwrapping:
    def test_pulls_the_real_url_out_of_a_tracking_wrapper(self):
        wrapped = "https://t.indeed.com/r?url=https%3A%2F%2Fuk.indeed.com%2Fviewjob%3Fjk%3Dabc&c=x"

        assert inbox.unwrap_redirect(wrapped) == "https://uk.indeed.com/viewjob?jk=abc"

    def test_leaves_a_plain_url_alone(self):
        url = "https://example.com/jobs/123"

        assert inbox.unwrap_redirect(url) == url

    def test_does_not_make_a_network_request(self, monkeypatch):
        # Following a tracking link server-side would register a click she
        # never made.
        import requests

        monkeypatch.setattr(
            requests,
            "get",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("no requests")),
        )

        inbox.unwrap_redirect("https://t.indeed.com/r?url=https%3A%2F%2Fx.com%2Fjobs%2F1")

    def test_survives_a_malformed_url(self):
        assert inbox.unwrap_redirect("http://[bad") == "http://[bad"


class TestJobLinkDetection:
    def test_recognises_job_paths(self):
        assert inbox.looks_like_a_job_link("https://x.com/jobs/view/123")
        assert inbox.looks_like_a_job_link("https://x.com/job/abc")
        assert inbox.looks_like_a_job_link("https://x.com/vacancies/1")
        assert inbox.looks_like_a_job_link("https://x.com/careers/eng-1")

    def test_recognises_indeeds_viewjob_path(self):
        # No slash before "job", so a slash-anchored pattern misses it.
        assert inbox.looks_like_a_job_link("https://uk.indeed.com/viewjob?jk=abc")

    def test_rejects_non_job_links(self):
        assert not inbox.looks_like_a_job_link("https://x.com/settings")
        assert not inbox.looks_like_a_job_link("mailto:someone@x.com")
        assert not inbox.looks_like_a_job_link("https://x.com/about")

    def test_rejects_listing_and_account_pages(self):
        # Every alert footer carries these.
        assert not inbox.looks_like_a_job_link("https://x.com/comm/jobs/search/")
        assert not inbox.looks_like_a_job_link("https://x.com/psettings/email")
        assert not inbox.looks_like_a_job_link("https://x.com/jobs/alerts")

    def test_a_job_title_containing_an_excluded_word_still_counts(self):
        # Regression: a substring test rejected this because "manager"
        # contains "manage", silently losing every manager vacancy.
        assert inbox.looks_like_a_job_link("https://x.com/jobs/operations-manager")
        assert inbox.looks_like_a_job_link("https://x.com/jobs/head-of-search")
        assert inbox.looks_like_a_job_link("https://x.com/jobs/profiler-role")


class TestExtraction:
    def test_pulls_titles_companies_and_links_from_a_linkedin_alert(self):
        jobs = inbox.extract_jobs(LINKEDIN_EMAIL, source_label="linkedin.com")

        assert [job.title for job in jobs] == [
            "Operations Associate",
            "Operations Coordinator",
        ]
        assert jobs[0].company == "Riverbank Trust"
        assert jobs[1].company == "Halcyon Group"
        assert jobs[0].url.startswith("https://www.linkedin.com/comm/jobs/view/3812345678")

    def test_ignores_footer_and_unsubscribe_links(self):
        jobs = inbox.extract_jobs(LINKEDIN_EMAIL, source_label="linkedin.com")

        assert all("See all" not in job.title for job in jobs)
        assert all("Unsubscribe" not in job.title for job in jobs)

    def test_handles_welcome_to_the_jungle(self):
        jobs = inbox.extract_jobs(WTTJ_EMAIL, source_label="welcometothejungle.com")

        assert len(jobs) == 1
        assert jobs[0].title == "Operations Manager"
        assert jobs[0].company == "Acme Studio"

    def test_unwraps_indeeds_tracking_link(self):
        jobs = inbox.extract_jobs(INDEED_EMAIL, source_label="indeed.com")

        assert jobs[0].url == "https://uk.indeed.com/viewjob?jk=abc123"

    def test_marks_the_thin_source_in_the_description(self):
        # So a lower score reads as "less information", not "the agent
        # dislikes this site".
        jobs = inbox.extract_jobs(LINKEDIN_EMAIL, source_label="linkedin.com")

        assert "limited detail" in jobs[0].description

    def test_deduplicates_within_one_email(self):
        html = """<html><body>
          <a href="https://x.com/jobs/1?utm=a">Operations Associate</a>
          <a href="https://x.com/jobs/1?utm=b">Operations Associate</a>
        </body></html>"""

        assert len(inbox.extract_jobs(html, source_label="x.com")) == 1

    def test_a_job_with_no_company_is_still_kept(self):
        # Better a duplicate than a missed vacancy -- the fingerprint falls
        # back to the URL so it can't wrongly merge with another employer's.
        html = '<html><body><a href="https://x.com/jobs/1">Operations Associate</a></body></html>'

        jobs = inbox.extract_jobs(html, source_label="x.com")

        assert len(jobs) == 1
        assert jobs[0].company == ""
        assert jobs[0].fingerprint.startswith("url:")

    def test_unrecognised_layout_yields_nothing_rather_than_rubbish(self):
        html = "<html><body><p>Nothing useful here at all.</p></body></html>"

        assert inbox.extract_jobs(html, source_label="x.com") == []

    def test_malformed_html_does_not_raise(self):
        assert isinstance(
            inbox.extract_jobs("<a href=", source_label="x.com"), list
        )

    def test_respects_the_per_message_cap(self):
        links = "".join(
            f'<a href="https://x.com/jobs/{i}">Role {i}</a>' for i in range(100)
        )
        jobs = inbox.extract_jobs(f"<html><body>{links}</body></html>", source_label="x")

        assert len(jobs) == inbox.MAX_JOBS_PER_MESSAGE


class TestTrustedSenders:
    def test_accepts_an_exact_domain(self):
        assert inbox.is_trusted("linkedin.com", ("linkedin.com",))

    def test_accepts_a_subdomain(self):
        assert inbox.is_trusted("e.linkedin.com", ("linkedin.com",))

    def test_rejects_a_lookalike_domain(self):
        # "notlinkedin.com" must not match "linkedin.com".
        assert not inbox.is_trusted("notlinkedin.com", ("linkedin.com",))

    def test_rejects_an_unlisted_sender(self):
        assert not inbox.is_trusted("spam.example", ("linkedin.com",))

    def test_empty_allowlist_trusts_nothing(self):
        # This mailbox is advertised on job sites, so anything can land in it.
        assert not inbox.is_trusted("linkedin.com", ())


class TestPlainTextFallback:
    def test_bare_urls_in_a_text_only_alert_are_found(self):
        from jobagent.sources.inbox import _plain_to_html

        html = _plain_to_html("New role: https://x.com/jobs/123 apply soon")

        assert 'href="https://x.com/jobs/123"' in html


class TestMailboxFailure:
    def test_missing_credentials_is_survivable(self):
        assert inbox.fetch("", "", _config()) == []

    def test_no_trusted_senders_reads_nothing(self):
        assert inbox.fetch("a@b.c", "pw", _config(inbox_trusted_senders=())) == []

    def test_a_broken_mailbox_does_not_cost_the_other_sources(self, monkeypatch):
        import imaplib

        def boom(*args, **kwargs):
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")

        monkeypatch.setattr(inbox.imaplib, "IMAP4_SSL", boom)

        assert inbox.fetch("a@b.c", "pw", _config()) == []


def _config(**overrides):
    from tests.test_sources import _config as base

    return base(**overrides)
