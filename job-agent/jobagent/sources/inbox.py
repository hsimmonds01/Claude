"""Read her job-alert emails over IMAP and turn them back into jobs.

This is the source that reaches every board with no usable API -- Welcome to
the Jungle, LinkedIn, Indeed -- without a line of scraping. She sets up a
saved search on any site she likes, it emails her as normal, and a forwarding
rule sends a copy here. Adding a new job site later needs no code at all.

**Two honest limitations, both by nature rather than by choice:**

1. Alert emails carry far less than an API does. Usually a title, a company
   and a link; rarely a salary, never a full description. Roles from here are
   therefore scored on thinner evidence and will tend to mark slightly lower.
   That is a property of the source, not a judgement on the site.
2. The extraction below is heuristic. Every provider lays its emails out
   differently and changes them without warning, so this is written to
   degrade -- a layout it doesn't recognise yields fewer jobs, never wrong
   ones, and never an exception.

Built against synthetic emails because no real mailbox existed yet. The
patterns need checking against genuine alerts once one does; a sudden drop to
zero jobs from a provider is the symptom to expect.
"""

from __future__ import annotations

import email
import imaplib
import logging
import re
from datetime import date, timedelta
from email.header import decode_header, make_header
from email.message import Message
from email.utils import getaddresses
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse

from ..models import Job

log = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
TIMEOUT = 60

# Stop one runaway newsletter from flooding a run.
MAX_MESSAGES = 200
MAX_JOBS_PER_MESSAGE = 40

# A link is a job link if its path mentions a job *and* isn't a listing or
# account page. Both halves are needed: requiring the keyword to follow a
# slash missed Indeed's "/viewjob", while accepting it anywhere let
# LinkedIn's "/comm/jobs/search/" footer link through as a vacancy.
#
# Deliberately broad on the keyword side: this has to work for boards nobody
# has thought of yet, since the whole point of this source is that she can add
# a site without anyone changing code.
_JOB_WORD = re.compile(
    r"(job|vacanc|career|opportunit|stellen|emploi|position|role)", re.IGNORECASE
)

# Listing, search and account pages, which appear in every alert footer.
# Matched against path *segments* rather than as raw substrings: a plain
# substring test rejected ".../jobs/operations-manager" because "manager"
# contains "manage".
_NOT_A_VACANCY = frozenset(
    {
        "search",
        "settings",
        "preferences",
        "unsubscribe",
        "manage",
        "alert",
        "alerts",
        "login",
        "signin",
        "register",
        "help",
        "privacy",
        "terms",
        "profile",
        "saved",
        "feed",
    }
)


def _is_listing_or_account_page(path: str) -> bool:
    """True for a search/listing/account page rather than one vacancy.

    Whole-segment match only. Anything looser starts eating real vacancies:
    substring matching rejected "operations-manager" (contains "manage"), and
    adding `endswith` rejected "head-of-search". Job titles are made of
    ordinary words, so the test has to be exact.

    Account pages like LinkedIn's "/psettings/email" need no entry here --
    they contain no job word, so the positive test rejects them already.
    """
    segments = {segment for segment in path.casefold().split("/") if segment}
    return bool(segments & _NOT_A_VACANCY)

# Query parameters that wrap the real destination inside a tracking redirect.
_REDIRECT_PARAMS = ("url", "u", "redirect", "target", "destination", "r")

# Chrome for "Apply now" style anchors that carry no title.
_NOT_A_TITLE = re.compile(
    r"^\s*(apply|apply now|view job|view|see more|more jobs|unsubscribe|"
    r"manage alerts?|settings|privacy|help|view all|show more|see all)\s*$",
    re.IGNORECASE,
)

# "Company · London" / "Company - London (Hybrid)" -- the line most alert
# emails put directly beneath the job title.
_COMPANY_LINE = re.compile(r"^\s*(?P<company>[^·|–—]{2,60}?)\s*[·|–—]\s*\S")


class _LinkExtractor(HTMLParser):
    """Collects a flat stream of links and text in document order.

    Order is what makes company detection possible: alert emails put the
    employer in the text immediately after the job-title link, so the stream
    lets us look just ahead of each link rather than guessing from CSS classes
    that change every redesign.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stream: list[tuple[str, str, str]] = []  # (kind, href, text)
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        self._href = dict(attrs).get("href") or ""
        self._text = []

    def handle_endtag(self, tag):
        if tag.lower() != "a" or self._href is None:
            return
        text = " ".join(" ".join(self._text).split())
        self.stream.append(("link", self._href, text))
        self._href = None
        self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)
        elif data.strip():
            self.stream.append(("text", "", " ".join(data.split())))

    def error(self, message):  # pragma: no cover - stdlib compatibility
        pass


def unwrap_redirect(url: str) -> str:
    """Follow a tracking wrapper to the real destination, without a request.

    Most providers keep the true URL in a query parameter, so this is a pure
    string operation -- no network call, and nothing that could look like
    click-tracking on her behalf.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    params = parse_qs(parsed.query)
    for key in _REDIRECT_PARAMS:
        for value in params.get(key, []):
            candidate = unquote(value)
            if candidate.startswith(("http://", "https://")):
                return candidate
    return url


def looks_like_a_job_link(url: str) -> bool:
    """True for a link to one vacancy, false for listings and account pages."""
    if not url.startswith(("http://", "https://")):
        return False
    try:
        path = urlparse(url).path
    except ValueError:
        return False
    if _is_listing_or_account_page(path):
        return False
    return bool(_JOB_WORD.search(path))


def _company_near(stream, index: int) -> str:
    """Best guess at the employer from the text just after a job link."""
    for kind, _href, text in stream[index + 1 : index + 4]:
        if kind != "text" or not text:
            continue
        match = _COMPANY_LINE.match(text)
        if match:
            return match.group("company").strip()
        # A short standalone line straight after the title is usually the
        # employer; a long one is blurb.
        if 2 <= len(text) <= 60 and not text.endswith("."):
            return text
    return ""


def extract_jobs(html: str, *, source_label: str, posted: str = "") -> list[Job]:
    """Pull candidate jobs out of one alert email's HTML."""
    parser = _LinkExtractor()
    try:
        parser.feed(html)
    except Exception as exc:  # noqa: BLE001 -- malformed mail must not crash a run
        log.warning("[inbox] could not parse an email body: %s", exc)
        return []

    jobs: list[Job] = []
    seen_urls: set[str] = set()

    for index, (kind, href, text) in enumerate(parser.stream):
        if kind != "link" or not text or _NOT_A_TITLE.match(text):
            continue
        url = unwrap_redirect(href)
        if not looks_like_a_job_link(url):
            continue
        key = url.split("?", 1)[0].rstrip("/").casefold()
        if key in seen_urls:
            continue
        seen_urls.add(key)

        jobs.append(
            Job(
                source="inbox",
                title=text,
                company=_company_near(parser.stream, index),
                url=url,
                location="",
                # No description: the ad body isn't in the email, and fetching
                # it would mean scraping the very sites this route exists to
                # avoid scraping.
                description=f"(From a {source_label} job alert email — "
                "limited detail available.)",
                posted=posted,
            )
        )
        if len(jobs) >= MAX_JOBS_PER_MESSAGE:
            break

    return jobs


def _body_html(message: Message) -> str:
    """Prefer the HTML part; fall back to plain text wrapped in anchors."""
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True) or b""
                return payload.decode(part.get_content_charset() or "utf-8", "replace")
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True) or b""
                return _plain_to_html(
                    payload.decode(part.get_content_charset() or "utf-8", "replace")
                )
        return ""

    payload = message.get_payload(decode=True) or b""
    text = payload.decode(message.get_content_charset() or "utf-8", "replace")
    if message.get_content_type() == "text/plain":
        return _plain_to_html(text)
    return text


def _plain_to_html(text: str) -> str:
    """Wrap bare URLs so the same extractor handles text-only alerts."""
    return re.sub(r"(https?://\S+)", r'<a href="\1">\1</a>', text)


def _sender_domains(message: Message) -> list[str]:
    """Every domain that genuinely sent this message.

    Must parse the header properly rather than pattern-matching it. A From
    header is `Display Name <address>`, and the display name is free text
    chosen by the sender -- so a regex search for the first email-shaped token
    happily returns the display name instead of the real address:

        From: "jobalerts@linkedin.com" <careers@attacker.example>

    That read as linkedin.com and passed the allowlist, which meant anyone who
    knew the mailbox address -- and it is advertised publicly on job sites --
    could get their links into her digest and onto her lock screen, from a
    domain of their own that passes SPF and DKIM and so lands in the inbox
    rather than in spam. `parseaddr` splits the two per RFC 5322, so only the
    real address can contribute.

    Returns every address found: a From carrying several is rare and not
    something a job board does, and `is_trusted_sender` requires all of them
    to be trusted rather than picking one and hoping.
    """
    raw = message.get("From", "")
    domains = []
    for _display_name, address in getaddresses([raw]):
        _, _, domain = address.rpartition("@")
        domain = domain.strip().casefold()
        if domain:
            domains.append(domain)
    return domains


def _subject(message: Message) -> str:
    try:
        return str(make_header(decode_header(message.get("Subject", ""))))
    except (ValueError, UnicodeDecodeError):
        return message.get("Subject", "")


def is_trusted(domain: str, trusted: tuple[str, ...]) -> bool:
    """Is one domain on the allowlist?

    An empty list means trust nothing. That's the safe default: this mailbox
    is publicly advertised on job sites, so anything can arrive in it, and
    treating a stranger's email as a job feed is how a link she shouldn't
    click ends up in her digest.
    """
    if not domain:
        return False
    return any(
        domain == allowed or domain.endswith("." + allowed) for allowed in trusted
    )


def is_trusted_sender(message: Message, trusted: tuple[str, ...]) -> bool:
    """Is this whole message from a sender we accept jobs from?

    Requires the From header to parse to at least one address, and every
    address in it to be trusted. Both halves matter: an unparseable From is
    rejected rather than defaulting to allowed, and a header listing a
    trusted address alongside an untrusted one doesn't get in on the strength
    of the trusted half.
    """
    domains = _sender_domains(message)
    if not domains:
        return False
    return all(is_trusted(domain, trusted) for domain in domains)


def fetch(address: str, app_password: str, config) -> list[Job]:
    """Read recent alert emails and return the jobs found in them."""
    if not address or not app_password:
        log.warning("[inbox] no mailbox credentials; skipping")
        return []
    if not config.inbox_trusted_senders:
        log.warning(
            "[inbox] no trusted_senders configured, so no email can be treated "
            "as a job alert. Add the sites you get alerts from under "
            "sources.alert_inbox.trusted_senders."
        )
        return []

    since = (date.today() - timedelta(days=config.inbox_max_age_days)).strftime(
        "%d-%b-%Y"
    )

    try:
        with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=TIMEOUT) as imap:
            imap.login(address, app_password)
            imap.select("INBOX", readonly=True)  # never alters her mailbox
            status, data = imap.search(None, "SINCE", since)
            if status != "OK":
                log.warning("[inbox] search failed: %s", status)
                return []

            ids = (data[0] or b"").split()[-MAX_MESSAGES:]
            log.info("[inbox] %d message(s) since %s", len(ids), since)

            jobs: list[Job] = []
            skipped = 0
            for message_id in ids:
                status, payload = imap.fetch(message_id, "(RFC822)")
                if status != "OK" or not payload or not payload[0]:
                    continue
                message = email.message_from_bytes(payload[0][1])

                if not is_trusted_sender(message, config.inbox_trusted_senders):
                    skipped += 1
                    continue

                domain = (_sender_domains(message) or [""])[0]
                found = extract_jobs(
                    _body_html(message), source_label=domain, posted=""
                )
                log.info(
                    "[inbox] %-24s %-45s -> %d job(s)",
                    domain,
                    _subject(message)[:45],
                    len(found),
                )
                jobs.extend(found)

            if skipped:
                log.info("[inbox] ignored %d message(s) from untrusted senders", skipped)
            return jobs

    except (imaplib.IMAP4.error, OSError) as exc:
        # A mailbox problem must not cost her the API sources or the digest.
        log.error("[inbox] could not read the mailbox: %s", exc)
        return []
