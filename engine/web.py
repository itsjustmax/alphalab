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
