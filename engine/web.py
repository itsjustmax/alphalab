"""web_fetch: the desk reads the member's links and learns from them.

A member pastes an example — a chart they admire, a write-up, a doc —
and the agent fetches the actual page: static GET, no keys, no JS
rendering. HTML is stripped to its text INCLUDING script bodies (for
chart examples, the code is the lesson); other text types pass through.
Bounded and clocked like every receipt.

Local and private addresses are off limits by structure: this tool
learns from the web, it does not probe the member's machine or network.
"""

import datetime
import html
import ipaddress
import json
import re
import urllib.parse
import urllib.request

MAX_FETCH_BYTES = 900_000
DEFAULT_CHARS = 20_000
MAX_CHARS = 60_000

_BLOCKED_HOSTS = {"localhost", "localhost.localdomain", "ip6-localhost"}


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds")


def receipt(summary, data=None, gaps=None, ok=True):
    return {"ok": ok, "summary": summary, "data": data or {},
            "as_of": _now(), "gaps": gaps or []}


def blocked_reason(url):
    """None when the URL may be fetched; otherwise the named refusal."""

    try:
        parts = urllib.parse.urlsplit(str(url or "").strip())
    except ValueError:
        return "the URL does not parse"
    if parts.scheme not in ("http", "https"):
        return f"only http(s) is fetchable, not {parts.scheme or 'nothing'!r}"
    host = (parts.hostname or "").strip("[]").lower()
    if not host:
        return "the URL names no host"
    if host in _BLOCKED_HOSTS or host.endswith(".local"):
        return "local addresses are off limits — this tool learns from the web"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if (address.is_private or address.is_loopback or address.is_link_local
            or address.is_reserved or address.is_multicast):
        return ("private and local addresses are off limits — this tool "
                "learns from the web")
    return None


_TAG_STRIP = re.compile(r"<(?:style)[^>]*>.*?</(?:style)>",
                        re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_BLANKS = re.compile(r"\n{3,}")


def html_to_text(markup):
    """Strip tags but keep script bodies — for examples, code is the lesson."""

    title_match = _TITLE.search(markup)
    title = html.unescape(title_match.group(1)).strip() if title_match else ""
    text = _TAG_STRIP.sub(" ", markup)
    text = text.replace("</p>", "\n").replace("</div>", "\n")
    text = text.replace("<br>", "\n").replace("<br/>", "\n")
    text = _TAGS.sub("", text)
    text = html.unescape(text)
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return title, _BLANKS.sub("\n\n", text).strip()


def _default_opener(url):
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (AlphaLab desk; +manifold harness)"})
    with urllib.request.urlopen(request, timeout=25) as response:
        content_type = response.headers.get("Content-Type", "")
        body = response.read(MAX_FETCH_BYTES + 1)
        return str(response.url), content_type, body


def web_fetch(arguments, opener=None):
    url = str(arguments.get("url") or "").strip()
    refusal = blocked_reason(url)
    if refusal:
        return receipt(f"refused: {refusal}", ok=False, gaps=[refusal])
    try:
        limit = min(int(arguments.get("max_chars") or DEFAULT_CHARS), MAX_CHARS)
    except (TypeError, ValueError):
        limit = DEFAULT_CHARS
    opener = opener or _default_opener
    try:
        final_url, content_type, body = opener(url)
    except Exception as error:
        return receipt(f"could not fetch {url}", ok=False,
                       gaps=[f"fetch failed: {str(error)[:300]}"])
    gaps = []
    if len(body) > MAX_FETCH_BYTES:
        body = body[:MAX_FETCH_BYTES]
        gaps.append(f"response bounded to {MAX_FETCH_BYTES} bytes")
    kind = content_type.split(";")[0].strip().lower()
    text = body.decode("utf-8", errors="replace")
    title = ""
    if kind in ("text/html", "application/xhtml+xml"):
        title, text = html_to_text(text)
    if len(text) > limit:
        text = text[:limit]
        gaps.append(f"text bounded to {limit} chars — raise max_chars "
                    f"(up to {MAX_CHARS}) for more")
    gaps.append("static fetch — no scripts ran; a page built entirely by "
                "JavaScript may read thin")
    return receipt(
        f"fetched {final_url}: {len(text)} chars of "
        f"{kind or 'unknown content'}"
        + (f" — {title[:80]}" if title else ""),
        {"url": final_url, "content_type": kind, "title": title,
         "chars": len(text), "text": text},
        gaps=gaps,
    )


def rss_fetch(arguments, opener=None):
    """One RSS/Atom feed → bounded story rows, clocked and sourced.

    The desk's news lane: agents wire a card per feed (a ticker's
    headline feed, a macro wire) and read the landed items next turn —
    stories worth keeping become findings with their link and clock.
    Same door policy as web_fetch: public http(s) only, never the
    member's machine.
    """

    import xml.etree.ElementTree as ElementTree

    url = str(arguments.get("url") or "").strip()
    blocked = blocked_reason(url)
    if blocked:
        return receipt(f"refused: {blocked}", ok=False, gaps=[blocked])
    limit = min(int(arguments.get("limit") or 12), 30)
    opener = opener or _default_opener
    try:
        _final_url, _content_type, raw = opener(url)
    except Exception as error:
        return receipt(f"the feed did not answer: {str(error)[:200]}",
                       ok=False, gaps=[str(error)[:200]])
    try:
        root = ElementTree.fromstring(raw.decode("utf-8", "replace"))
    except ElementTree.ParseError as error:
        return receipt(f"the feed is not parseable XML: {str(error)[:120]}",
                       ok=False, gaps=[f"parse error: {str(error)[:120]}"])

    def _text(node, *names):
        for name in names:
            for child in node.iter():
                tag = child.tag.rsplit("}", 1)[-1]
                if tag == name and (child.text or "").strip():
                    return child.text.strip()
        return ""

    def _first(node, *names):
        for child in node:
            if child.tag.rsplit("}", 1)[-1] in names:
                return child
        return None

    items = []
    feed_title = ""
    channel = _first(root, "channel")
    entries = []
    if channel is not None:                      # RSS 2.0
        title_node = _first(channel, "title")
        feed_title = (title_node.text or "").strip() if title_node is not None else ""
        entries = [child for child in channel
                   if child.tag.rsplit("}", 1)[-1] == "item"]
    elif root.tag.rsplit("}", 1)[-1] == "feed":  # Atom
        title_node = _first(root, "title")
        feed_title = (title_node.text or "").strip() if title_node is not None else ""
        entries = [child for child in root
                   if child.tag.rsplit("}", 1)[-1] == "entry"]
    for entry in entries[:limit]:
        title_node = _first(entry, "title")
        link_node = _first(entry, "link")
        link = ""
        if link_node is not None:
            link = (link_node.get("href") or link_node.text or "").strip()
        summary = _text(entry, "description", "summary", "content")
        _summary_title, summary_text = html_to_text(summary)
        items.append({
            "title": (title_node.text or "").strip()
            if title_node is not None else "(untitled)",
            "link": link,
            "published": _text(entry, "pubDate", "published", "updated"),
            "summary": summary_text[:300],
        })
    if not items:
        return receipt(f"{url}: the feed parsed but carries no items",
                       ok=False, gaps=["no <item> or <entry> elements"])
    return receipt(
        f"{feed_title or url}: {len(items)} stor{'y' if len(items) == 1 else 'ies'}",
        {"feed_title": feed_title, "url": url, "items": items})
