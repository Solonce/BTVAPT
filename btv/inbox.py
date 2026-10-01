"""Leads from outside the scrapers.

* ``ingest_text``: one post/message (from the bookmarklet, the Add page or an
  email) becomes a listing in the ``manual``/``email`` source and goes
  through the normal pipeline (dates, address linking, contacts, scam check).
* ``inbox`` job: reads unseen mail from an IMAP folder you point Facebook
  group notifications, Front Porch Forum digests and saved-search alerts at.

Configure in btv.toml::

    [inbox]
    host = "imap.gmail.com"
    user = "you+btv@gmail.com"
    password = "app-password"     # or env BTV_INBOX_PASSWORD
    folder = "INBOX"
"""

from __future__ import annotations

import email
import email.policy
import hashlib
import imaplib
import os
import re
from email.utils import parsedate_to_datetime

from sqlalchemy.orm import Session

from btv.archive import record_fetch
from btv.db import session_factory, utcnow
from btv.ingest import upsert_listing
from btv.jobs.runner import JobContext, job
from btv.models import Source, SourceListing
from btv.normalize.freeform import parse_freeform
from btv.sources.base import ParsedListing, html_to_text

RENTAL_HINT = re.compile(
    r"(?i)\b(for\s+rent|rental|apartment|apt\b|bedroom|\dbr\b|studio|sublet|sublease|lease|roommate|housemate|"
    r"room\s+available|available\s+(?:june|july|aug|sept|now)|landlord|move[- ]in)\b")
_LINK = re.compile(r"https?://[^\s<>\"')]+")
_PREFERRED_LINK = re.compile(r"(?i)facebook\.com/(?:groups|marketplace|permalink|share)|frontporchforum\.com|craigslist\.org")


def ingest_text(session: Session, text: str, *, source_id: str = "manual", url: str | None = None,
                title: str | None = None, author: str | None = None, via: str = "manual",
                received_at=None) -> SourceListing:
    source = session.get(Source, source_id)
    if source is None:
        raise ValueError(f"source {source_id!r} not configured (run `btv sources sync`)")
    text = (text or "").strip()
    if not text and not url:
        raise ValueError("need text or a URL")
    links = _LINK.findall(text)
    if not url:
        url = next((u for u in links if _PREFERRED_LINK.search(u)), None) or (links[0] if links else None)
    ext = hashlib.sha256((url or text).encode()).hexdigest()[:24]
    fetch = record_fetch(session, url=url or f"{via}:{ext}", body=text.encode(), http_status=None,
                         content_type="text/plain", via=via)
    f = parse_freeform(text)
    pl = ParsedListing(
        external_id=ext,
        url=url or f"btv:lead/{ext}",
        title=title or f["title"],
        address_raw=f["address"],
        rent=f["rent"],
        beds=f["beds"],
        baths=f["baths"],
        description=text,
        pets=f["pets"],
        contact_raw={"name": author} if author else None,
        extra={"platform": via, "author": author, "received_at": (received_at or utcnow()).isoformat(),
               "private_landlord": True, "lead": True},
        raw_fetch_id=fetch.id,
    )
    sl, _ = upsert_listing(session, source, None, pl, f"{via}-1")
    if sl.triage is None:
        sl.triage = "new"
    session.flush()
    return sl


def _body(msg: email.message.EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    content = part.get_content()
    if part.get_content_type() == "text/html":
        content = html_to_text(re.sub(r"(?is)<(style|script).*?</\1>", "", content)) or ""
    return content


@job("inbox")
def inbox_job(ctx: JobContext, **_ignored) -> dict:
    conf = ctx.cfg.extra.get("inbox") or {}
    if not conf.get("host"):
        return {"skipped": "no [inbox] configured in btv.toml"}
    password = os.environ.get("BTV_INBOX_PASSWORD") or conf.get("password")
    stats = {"messages": 0, "leads": 0, "ignored": 0}
    session = session_factory(ctx.cfg)()
    imap = imaplib.IMAP4_SSL(conf["host"], int(conf.get("port", 993)))
    try:
        imap.login(conf["user"], password)
        imap.select(conf.get("folder", "INBOX"))
        ctx.step("checking for new mail")
        _, data = imap.search(None, "UNSEEN")
        ids = data[0].split()
        ctx.set_total(len(ids))
        for num in ids:
            _, parts = imap.fetch(num, "(RFC822)")
            raw = parts[0][1]
            msg = email.message_from_bytes(raw, policy=email.policy.default)
            stats["messages"] += 1
            subject = str(msg.get("subject") or "")
            sender = str(msg.get("from") or "")
            text = f"{subject}\n\n{_body(msg)}"
            record_fetch(session, url=f"email:{msg.get('message-id', num.decode())}", body=raw, http_status=None,
                         content_type="message/rfc822", via="email")
            if RENTAL_HINT.search(text):
                when = None
                try:
                    when = parsedate_to_datetime(msg.get("date")).replace(tzinfo=None)
                except (TypeError, ValueError):
                    pass
                ingest_text(session, text, source_id="email", title=subject[:120] or None,
                            author=re.sub(r"<.*?>", "", sender).strip() or None, via="email", received_at=when)
                stats["leads"] += 1
            else:
                stats["ignored"] += 1
            session.commit()
            ctx.advance(step=subject[:60])
        return stats
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001
            pass
        session.close()
