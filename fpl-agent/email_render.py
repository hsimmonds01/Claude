"""HTML for the deadline emails.

Read on a phone, usually standing up, shortly before a deadline. So the
verdict goes at the top in one line, and the reasoning sits underneath for
when there's time to care about it.

Every number rendered here comes from the model or the optimiser. This file
formats; it never calculates and never estimates. All model output is escaped
before it reaches the HTML, because some of it (news concerns, injury
strings) originates from feeds and a language model.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone

SEVERITY_STYLES = {
    "high": ("#b91c1c", "#fee2e2"),
    "medium": ("#b45309", "#fef3c7"),
    "low": ("#1d4ed8", "#dbeafe"),
}
FONT = "-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def safe_url(url: str) -> str:
    url = (url or "").strip()
    return html.escape(url, quote=True) if url.lower().startswith(("http://", "https://")) else ""


def card(inner: str, accent: str = "#e5e7eb") -> str:
    return (f'<div style="background:#fff;border:1px solid {accent};border-radius:12px;'
            f'padding:16px 18px;margin-bottom:14px;">{inner}</div>')


def heading(text: str) -> str:
    return (f'<div style="font-size:12px;font-weight:700;letter-spacing:0.6px;color:#6b7280;'
            f'text-transform:uppercase;margin:22px 0 8px;">{esc(text)}</div>')


def render_verdict(verdict: str, subtitle: str) -> str:
    return (f'<div style="background:#111827;border-radius:14px;padding:22px 20px;margin-bottom:16px;">'
            f'<div style="font-size:19px;font-weight:800;color:#fff;line-height:1.35;">{esc(verdict)}</div>'
            f'<div style="font-size:13px;color:#9ca3af;margin-top:6px;">{esc(subtitle)}</div></div>')


def render_transfers(options: list[dict], free_transfers: int, bank: float) -> str:
    if not options:
        return card('<div style="font-size:14px;color:#374151;">No transfer clears the bar this week. '
                    'Rolling the free transfer is the recommendation.</div>')
    rows = []
    for index, option in enumerate(options[:3]):
        best = index == 0
        gain = option["gain"]
        verdict = "recommended" if best and gain > 0.5 else "alternative"
        colour = "#065f46" if best and gain > 0.5 else "#374151"
        rows.append(
            f'<div style="padding:10px 0;{"border-top:1px solid #f3f4f6;" if index else ""}">'
            f'<div style="font-size:15px;font-weight:700;color:{colour};">'
            f'{esc(option["out"]["name"])} &rarr; {esc(option["in"]["name"])}</div>'
            f'<div style="font-size:13px;color:#6b7280;margin-top:3px;">'
            f'{gain:+.1f} projected pts over the next 5 &middot; '
            f'£{option["in"]["price"]:.1f}m &middot; {option["in"]["selected_by"]:.0f}% owned &middot; '
            f'bank after £{option["bank_after"]:.1f}m &middot; {esc(verdict)}</div></div>'
        )
    header = (f'<div style="font-size:13px;color:#6b7280;margin-bottom:6px;">'
              f'{free_transfers} free transfer(s), £{bank:.1f}m in the bank. '
              f'Doing nothing scores 0.0 &mdash; a move has to beat that.</div>')
    return card(header + "".join(rows))


def render_team(starters: list[dict], bench: list[dict], captain: dict) -> str:
    rows = []
    for player in starters:
        mark = ' <span style="color:#b45309;font-weight:700;">(C)</span>' if player is captain else ""
        rows.append(
            f'<tr><td style="padding:5px 0;font-size:14px;color:#111827;">{esc(player["name"])}{mark}</td>'
            f'<td style="font-size:12px;color:#6b7280;">{esc(player["team"])} {esc(player["position"])}</td>'
            f'<td style="font-size:13px;color:#111827;text-align:right;">{player["total"]:.1f}</td></tr>'
        )
    bench_row = " &middot; ".join(f'{esc(p["name"])}' for p in bench)
    return card(
        '<table style="width:100%;border-collapse:collapse;">' + "".join(rows) + "</table>"
        f'<div style="font-size:12px;color:#6b7280;margin-top:10px;border-top:1px solid #f3f4f6;'
        f'padding-top:8px;">Bench, in order: {bench_row}</div>'
    )


def render_captain(shortlist: list[dict]) -> str:
    rows = []
    for index, player in enumerate(shortlist[:4]):
        label = "safe pick" if player["selected_by"] >= 20 else "differential"
        weight = 700 if index == 0 else 500
        rows.append(
            f'<div style="padding:7px 0;{"border-top:1px solid #f3f4f6;" if index else ""}">'
            f'<span style="font-size:14px;font-weight:{weight};color:#111827;">{esc(player["name"])}</span>'
            f'<span style="font-size:12px;color:#6b7280;"> &middot; {player["total"]:.1f} projected '
            f'&middot; {player["selected_by"]:.0f}% owned &middot; {label}</span></div>'
        )
    return card("".join(rows))


def render_news(flags: list[dict]) -> str:
    if not flags:
        return ""
    rows = []
    for flag in flags:
        colour, background = SEVERITY_STYLES.get(flag.get("severity", "low"), SEVERITY_STYLES["low"])
        link = safe_url(flag.get("source", ""))
        source = (f'<a href="{link}" style="color:#6b7280;font-size:12px;">source</a>' if link else "")
        rows.append(
            f'<div style="padding:9px 0;">'
            f'<span style="display:inline-block;font-size:10px;font-weight:700;color:{colour};'
            f'background:{background};border-radius:999px;padding:2px 8px;text-transform:uppercase;">'
            f'{esc(flag.get("severity", "low"))}</span> '
            f'<span style="font-size:14px;font-weight:600;color:#111827;">{esc(flag["player"])}</span>'
            f'<div style="font-size:13px;color:#374151;margin-top:3px;">{esc(flag["concern"])} {source}</div>'
            f'</div>'
        )
    return card("".join(rows), accent="#fecaca")


def render_gap(gap: list[dict]) -> str:
    if not gap:
        return ""
    rows = [
        f'<div style="font-size:13px;color:#374151;padding:4px 0;">'
        f'<b>{esc(p["name"])}</b> ({esc(p["team"])}) &middot; {p["selected_by"]:.0f}% owned '
        f'&middot; £{p["price"]:.1f}m &middot; exposure {p["exposure"]:.1f} pts</div>'
        for p in gap[:5]
    ]
    note = ('<div style="font-size:12px;color:#6b7280;margin-top:8px;">Exposure is what you concede '
            'to the average rival by not owning him.</div>')
    return card("".join(rows) + note)


def render_prices(movers: list[dict]) -> str:
    if not movers:
        return ""
    rows = []
    for mover in movers[:6]:
        arrow = "&#9650;" if mover["direction"] == "rise" else "&#9660;"
        colour = "#065f46" if mover["direction"] == "rise" else "#b91c1c"
        rows.append(
            f'<div style="font-size:13px;color:#374151;padding:4px 0;">'
            f'<span style="color:{colour};">{arrow}</span> <b>{esc(mover["name"])}</b> '
            f'£{mover["price"]:.1f}m &middot; {mover["probability"]:.0%} chance of a '
            f'{esc(mover["direction"])} &middot; <span style="color:#6b7280;">'
            f'{esc(mover.get("confidence", ""))}</span></div>'
        )
    return card("".join(rows))


def render_email(*, verdict: str, subtitle: str, sections: list[tuple[str, str]],
                 footer_note: str = "") -> str:
    body = []
    for title, content in sections:
        if not content:
            continue
        if title:
            body.append(heading(title))
        body.append(content)
    stamp = datetime.now(timezone.utc).strftime("%a %d %b, %H:%M UTC")
    return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f3f4f6;">
<div style="max-width:600px;margin:0 auto;padding:22px 16px;font-family:{FONT};">
{render_verdict(verdict, subtitle)}
{"".join(body)}
<div style="text-align:center;padding:18px 8px;font-size:11px;color:#9ca3af;line-height:1.6;">
Projections from fpl-agent &middot; generated {esc(stamp)}<br>
{esc(footer_note)}<br>
Tune behaviour by editing <b>fpl-agent/strategy.md</b>.
</div></div></body></html>"""
