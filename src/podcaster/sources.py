"""Read the source material for an episode: every supported file under a folder, recursively.

Text (.md, .txt) is read as is. PDF pages come through pypdf with a `# page N` marker each.
HTML files go through trafilatura for the main text. A .url file holds one URL per line and is
fetched only when the caller passes `fetch=True`; by default nothing leaves the machine and the
file is listed as skipped. Every file gets a `# source:` header. A word cap truncates with a
visible `# truncated` marker, never silently.
"""

from __future__ import annotations

import sys
from ipaddress import ip_address, ip_network
from pathlib import Path
from socket import gaierror, getaddrinfo
from typing import Callable
from urllib.parse import urlparse

TEXT = (".md", ".txt")
PDF = (".pdf",)
HTML = (".html", ".htm")
URLS = (".url",)
EXTENSIONS = TEXT + PDF + HTML + URLS
DEFAULT_MAX_WORDS = 20000

Fetch = Callable[[str], str]  # url -> html


def source_files(folder: Path) -> list[Path]:
    """Readable files under `folder`. Symlinks are skipped: see readable_file()."""
    return sorted(p for p in folder.rglob("*") if readable_file(p) and p.suffix.lower() in EXTENSIONS)


def readable_file(path: Path) -> bool:
    """True for a real file this repo will read. A symlink is never one.

    `is_file()` follows the link, so one `notes.md -> ~/.ssh/id_rsa` inside a folder handed to
    --source becomes source text and, under a writer that leaves the machine, leaves with it.
    A folder is pointed at material, not curated file by file, so the link is the case to
    refuse rather than the one to trust.
    """
    return path.is_file() and not path.is_symlink()


def read_pdf(path: Path) -> str:
    from pypdf import PdfReader

    pages = []
    for n, page in enumerate(PdfReader(str(path)).pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(f"# page {n}\n\n{text}")
    return "\n\n".join(pages)


def read_html(html: str, url: str | None = None) -> str:
    import trafilatura

    return (trafilatura.extract(html, url=url, include_comments=False, include_tables=True) or "").strip()


PRIVATE_NETS = (
    ip_network("127.0.0.0/8"),
    ip_network("::1/128"),
    ip_network("10.0.0.0/8"),
    ip_network("172.16.0.0/12"),
    ip_network("192.168.0.0/16"),
    ip_network("169.254.0.0/16"),
    ip_network("fc00::/7"),
    ip_network("fe80::/10"),
)


def check_url(url: str) -> None:
    """Refuse a URL this repo should not fetch. Raises OSError naming the reason.

    Two checks. The scheme must be http or https, so a `.url` file cannot reach `file://` or
    anything else the library would happily open. The host must not resolve into loopback,
    link-local or private space: this is the only outbound path in the repo, and whatever
    comes back goes into the writer's prompt and from there into a script, so a URL pointing
    at a local service would read that service's response into the episode.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise OSError(f"refusing {url}: only http and https are fetched")
    if not parsed.hostname:
        raise OSError(f"refusing {url}: no host")
    try:
        infos = getaddrinfo(parsed.hostname, None)
    except gaierror as e:
        raise OSError(f"refusing {url}: {parsed.hostname} does not resolve ({e})") from e
    for info in infos:
        address = ip_address(info[4][0])
        for net in PRIVATE_NETS:
            if address.version == net.version and address in net:
                raise OSError(f"refusing {url}: {parsed.hostname} resolves to {address}, a private address")


def default_fetch(url: str) -> str:
    import trafilatura

    check_url(url)
    downloaded = trafilatura.fetch_url(url)
    if downloaded is None:
        raise OSError(f"could not fetch {url}")
    return downloaded


def read_one(path: Path, fetch: Fetch | None) -> tuple[str, list[str]]:
    """Return (text, notes). Notes are stderr lines about what was skipped."""
    suffix = path.suffix.lower()
    if suffix in TEXT:
        return path.read_text().strip(), []
    if suffix in PDF:
        return read_pdf(path), []
    if suffix in HTML:
        return read_html(path.read_text(errors="replace")), []
    urls = [ln.strip() for ln in path.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    if fetch is None:
        return "", [f"skipped {path.name}: {len(urls)} url(s); pass --fetch to allow network"]
    parts, notes = [], []
    for url in urls:
        try:
            parts.append(f"# url: {url}\n\n{read_html(fetch(url), url)}")
        except OSError as e:
            notes.append(f"skipped {url}: {e}")
    return "\n\n".join(parts), notes


def read_sources(folder: Path, fetch: Fetch | None = None, max_words: int = DEFAULT_MAX_WORDS) -> str:
    """Concatenate the files with a header naming each one, relative to `folder`, capped at `max_words`."""
    parts: list[str] = []
    for p in source_files(folder):
        try:
            text, notes = read_one(p, fetch)
        except Exception as e:  # a corrupt PDF or HTML file skips with a note; the rest of the folder still reads
            text, notes = "", [f"skipped {p.relative_to(folder)}: {type(e).__name__}: {e}"]
        for note in notes:
            print(f"sources: {note}", file=sys.stderr)
        if text:
            parts.append(f"# source: {p.relative_to(folder)}\n\n{text}\n")
    joined = "\n".join(parts)
    words = joined.split()
    if len(words) > max_words:
        print(f"sources: {len(words)} words, cut to {max_words}; raise [sources] max_words to keep more", file=sys.stderr)
        return " ".join(words[:max_words]) + f"\n\n# truncated: {len(words) - max_words} words cut\n"
    return joined
