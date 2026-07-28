"""Fetch and parse Listings Project NYC index pages (server-side; no browser CORS)."""

from __future__ import annotations

import html as html_lib
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Literal

import httpx

BASE_URL = "https://www.listingsproject.com/real-estate/new-york-city"
FIRST_ACCESS_BASE = "https://www.listingsproject.com/real-estate/first-access/new-york-city"
FIRST_ACCESS_CATEGORIES: tuple[str, ...] = (
    "rentals",
    "studios",
    "sublets",
    "seeking_living",
)

CARD_SPLIT_RE = re.compile(
    r"""<div\s+class=["']flex flex-col md:flex-row[^"']*["']>""",
    re.IGNORECASE,
)
HOOD_RE = re.compile(
    r"""<div class="text-grey-dark[^"]*\btext-smish\b[^"]*"[^>]*>\s*([^<]+)""",
    re.DOTALL,
)
TITLE_LINK_RE = re.compile(
    r"""<a class="text-teal-light hover:text-teal no-underline"[^>]*?href="([^"]+)">([^<]+)</a>""",
    re.DOTALL,
)
PRICE_RE = re.compile(
    r"""(\$\s*[0-9][0-9,]*(?:/[A-Za-z]+)?)\s*</span>""",
    re.DOTALL,
)
DATE_FINDALL_RE = re.compile(r"([A-Za-z]+\s+\d{1,2},\s+\d{4})")
PAGE_NUM_RE = re.compile(r"page=(\d+)")
DESC_RE = re.compile(
    r"""<p class="text-sm leading-normal mb-4"[^>]*>(.*?)</p>""",
    re.DOTALL,
)
SEE_MORE_RE = re.compile(
    r"""<a\b[^>]*>\s*See more\s*</a>""",
    re.IGNORECASE,
)

BoroughKey = Literal["all", "brooklyn", "queens", "bronx", "staten_island", "manhattan"]
SearchMode = Literal["on_or_after", "overlap", "on_date"]

BOROUGH_LABELS: dict[BoroughKey, str] = {
    "all": "All NYC",
    "manhattan": "Manhattan",
    "brooklyn": "Brooklyn",
    "queens": "Queens",
    "bronx": "Bronx",
    "staten_island": "Staten Island",
}


def _extract_listing_thumb_url(chunk: str) -> str | None:
    """First index-card photo URL in a listing HTML chunk, if any."""
    for m in re.finditer(r"<img[^>]+>", chunk, flags=re.I):
        tag = m.group(0)
        if "newsletter-listing-index-image" not in tag.lower():
            continue
        sm = re.search(r'src\s*=\s*"([^"]+)"', tag, flags=re.I)
        if sm:
            return html_lib.unescape(sm.group(1).strip())
    return None


@dataclass(frozen=True)
class ListingRow:
    title: str
    # Area + borough from the card line, e.g. "Greenpoint, Brooklyn"
    neighborhood: str
    neighborhood_name: str
    borough_label: str
    borough_key: BoroughKey
    listing_type: str
    description: str
    # HTTPS thumbnail from the index card (bunny.net CDN), if present
    photo_url: str | None
    availability: str
    listing_start: datetime
    listing_end: datetime
    price: str
    url: str
    is_first_access: bool = False


def _parse_listing_date(s: str) -> datetime | None:
    s = s.strip()
    for fmt in ("%B %d, %Y", "%B %e, %Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _normalize_title(raw: str) -> str:
    t = html_lib.unescape(raw)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _normalize_neighborhood_line(raw: str) -> str:
    """Card location line without trailing marketing (e.g. drop \" | Apartments for Sublet\")."""
    t = _normalize_title(raw)
    if "|" in t:
        t = t.split("|", 1)[0].strip()
    return t


def _extract_description(chunk: str) -> str:
    dm = DESC_RE.search(chunk)
    if not dm:
        return ""
    body = SEE_MORE_RE.sub("", dm.group(1))
    body = re.sub(r"<[^>]+>", " ", body)
    return _normalize_title(body)


def _derive_borough_key(location_line: str) -> BoroughKey:
    h = location_line.strip()
    if re.search(r"\bBrooklyn\b", h):
        return "brooklyn"
    if re.search(r"\bQueens\b", h):
        return "queens"
    if re.search(r"\bBronx\b", h):
        return "bronx"
    if re.search(r"Staten Island", h, re.I):
        return "staten_island"
    if re.search(r"\bManhattan\b", h):
        return "manhattan"
    if ", New York" in h:
        return "manhattan"
    return "all"


def _parse_location_line(raw_line: str) -> tuple[str, str, str, BoroughKey]:
    """
    Parse a card location line into neighborhood_name, borough_label, listing_type, borough_key.

    Example: ``Park Slope, Brooklyn | Apartments for Sublet`` ->
    (``Park Slope``, ``Brooklyn``, ``Apartments for Sublet``, ``brooklyn``).
    """
    normalized = _normalize_title(raw_line)
    parts = [p.strip() for p in normalized.split("|")]
    first_segment = parts[0] if parts else normalized
    listing_type = parts[1] if len(parts) > 1 else ""

    borough_key = _derive_borough_key(first_segment)
    if borough_key == "all":
        borough_label = ""
    else:
        borough_label = BOROUGH_LABELS[borough_key]

    if ", " in first_segment:
        neighborhood_name = first_segment.rsplit(", ", 1)[0].strip()
    else:
        neighborhood_name = first_segment.strip()

    return neighborhood_name, borough_label, listing_type, borough_key


def _absolute_url(href: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return f"https://www.listingsproject.com{href}"


def _hood_matches_borough(hood_raw: str, borough: BoroughKey) -> bool:
    if borough == "all":
        return True
    h = hood_raw.strip()
    if borough == "brooklyn":
        return bool(re.search(r"\bBrooklyn\b", h))
    if borough == "queens":
        return bool(re.search(r"\bQueens\b", h))
    if borough == "bronx":
        return bool(re.search(r"\bBronx\b", h))
    if borough == "staten_island":
        return bool(re.search(r"Staten Island", h, re.I))
    if borough == "manhattan":
        if re.search(r"\bManhattan\b", h):
            return True
        if re.search(r"\bQueens\b|\bBrooklyn\b|\bBronx\b|Staten Island", h, re.I):
            return False
        return ", New York" in h
    return True


def _row_from_parsed(row: dict, *, is_first_access: bool = False) -> ListingRow:
    return ListingRow(
        title=row["title"],
        neighborhood=row["hood_line"],
        neighborhood_name=row["neighborhood_name"],
        borough_label=row["borough_label"],
        borough_key=row["borough_key"],
        listing_type=row["listing_type"],
        description=row["description"],
        photo_url=row["photo_url"],
        availability=f"{row['start_date_str']} to {row['end_date_str']}",
        listing_start=row["listing_start"],
        listing_end=row["listing_end"],
        price=row["price"],
        url=row["url"],
        is_first_access=is_first_access,
    )


def parse_listings_from_html(html: str, borough: BoroughKey = "all") -> list[dict]:
    """Parse listing cards from one index page HTML. No dedupe."""
    out: list[dict] = []
    chunks = CARD_SPLIT_RE.split(html)
    for chunk in chunks[1:]:
        if "text-teal-light hover:text-teal no-underline" not in chunk:
            continue
        hm = HOOD_RE.search(chunk)
        if not hm:
            continue
        raw_location_line = _normalize_title(hm.group(1))
        hood_line = _normalize_neighborhood_line(raw_location_line)
        if not _hood_matches_borough(hood_line, borough):
            continue
        tm = TITLE_LINK_RE.search(chunk)
        if not tm:
            continue
        href, title_raw = tm.group(1), tm.group(2)
        pm = PRICE_RE.search(chunk)
        price = pm.group(1).strip() if pm else "N/A"
        date_strings = DATE_FINDALL_RE.findall(chunk)
        if len(date_strings) < 1:
            continue
        start_s = date_strings[0]
        end_s = date_strings[1] if len(date_strings) > 1 else start_s
        start_dt = _parse_listing_date(start_s)
        end_dt = _parse_listing_date(end_s)
        if start_dt is None or end_dt is None:
            continue
        title = _normalize_title(title_raw)
        url = _absolute_url(href)
        neighborhood_name, borough_label, listing_type, borough_key = _parse_location_line(
            raw_location_line
        )
        out.append(
            {
                "listing_start": start_dt,
                "listing_end": end_dt,
                "start_date_str": start_s.strip(),
                "end_date_str": end_s.strip(),
                "price": price,
                "title": title,
                "url": url,
                "hood_line": hood_line,
                "neighborhood_name": neighborhood_name,
                "borough_label": borough_label,
                "borough_key": borough_key,
                "listing_type": listing_type,
                "description": _extract_description(chunk),
                "photo_url": _extract_listing_thumb_url(chunk),
            }
        )
    return out


def discover_max_page(html: str) -> int:
    nums = [int(m.group(1)) for m in PAGE_NUM_RE.finditer(html)]
    return max(nums) if nums else 1


def fetch_page(client: httpx.Client, url: str, page: int = 1) -> str:
    r = client.get(url, params={"page": page})
    r.raise_for_status()
    return r.text


def _append_parsed_page(
    html: str,
    *,
    is_first_access: bool,
    seen_urls: set[str],
    results: list[ListingRow],
) -> None:
    for row in parse_listings_from_html(html, "all"):
        url = row["url"]
        if url in seen_urls:
            continue
        seen_urls.add(url)
        results.append(_row_from_parsed(row, is_first_access=is_first_access))


def fetch_all_listings(
    *,
    request_delay_s: float = 0.25,
    progress: Callable[[int, int], None] | None = None,
    client: httpx.Client | None = None,
    cookie: str | None = None,
) -> list[ListingRow]:
    """Crawl NYC public index (and first-access when ``cookie`` is set); dedupe by URL."""
    own_client = client is None
    if own_client:
        headers: dict[str, str] = {"User-Agent": "ListingProjectLocalTool/1.0"}
        if cookie:
            headers["Cookie"] = cookie
        client = httpx.Client(timeout=30.0, headers=headers)

    indexes: list[tuple[str, bool]] = [(BASE_URL, False)]
    if cookie:
        for category in FIRST_ACCESS_CATEGORIES:
            indexes.append((f"{FIRST_ACCESS_BASE}/{category}", True))

    seen_urls: set[str] = set()
    results: list[ListingRow] = []

    try:
        # Discover page counts for every index first so progress is one continuous bar.
        discovered: list[tuple[str, bool, str, int]] = []
        for i, (base_url, is_first_access) in enumerate(indexes):
            if i > 0 and request_delay_s > 0:
                time.sleep(request_delay_s)
            html = fetch_page(client, base_url, page=1)
            max_page = discover_max_page(html)
            discovered.append((base_url, is_first_access, html, max_page))

        total_pages = sum(max_page for _, _, _, max_page in discovered)
        done = 0

        for base_url, is_first_access, first_html, max_page in discovered:
            html = first_html
            for page_num in range(1, max_page + 1):
                if page_num > 1:
                    if request_delay_s > 0:
                        time.sleep(request_delay_s)
                    html = fetch_page(client, base_url, page=page_num)
                _append_parsed_page(
                    html,
                    is_first_access=is_first_access,
                    seen_urls=seen_urls,
                    results=results,
                )
                done += 1
                if progress:
                    progress(done, total_pages)
    finally:
        if own_client:
            client.close()

    return results


def search_listings(
    mode: SearchMode,
    borough: BoroughKey,
    *,
    on_or_after: date | None = None,
    overlap_start: date | None = None,
    overlap_end: date | None = None,
    on_date: date | None = None,
    request_delay_s: float = 0.25,
    progress: Callable[[int, int], None] | None = None,
    client: httpx.Client | None = None,
) -> list[ListingRow]:
    """
    Crawl all NYC index pages, parse, dedupe by URL, apply borough + date filters.

    ``on_or_after`` is used when mode is ``on_or_after`` (listing start >= that date at local midnight).
    ``overlap_start`` / ``overlap_end`` when mode is ``overlap`` (inclusive interval overlap).
    ``on_date`` when mode is ``on_date`` (listing window covers that single date).
    """
    if mode == "on_or_after":
        if on_or_after is None:
            raise ValueError("on_or_after date is required for mode on_or_after")
        cutoff = datetime.combine(on_or_after, datetime.min.time())
    elif mode == "on_date":
        if on_date is None:
            raise ValueError("on_date is required for mode on_date")
        target = datetime.combine(on_date, datetime.min.time())
    else:
        if overlap_start is None or overlap_end is None:
            raise ValueError("overlap_start and overlap_end are required for mode overlap")
        range_start = datetime.combine(overlap_start, datetime.min.time())
        range_end = datetime.combine(overlap_end, datetime.min.time())

    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=30.0, headers={"User-Agent": "ListingProjectLocalTool/1.0"})

    seen_urls: set[str] = set()
    results: list[ListingRow] = []

    try:
        html = fetch_page(client, BASE_URL, page=1)
        max_page = discover_max_page(html)
        page_num = 1
        while page_num <= max_page:
            if progress:
                progress(page_num, max_page)
            for row in parse_listings_from_html(html, borough):
                url = row["url"]
                if url in seen_urls:
                    continue
                ls: datetime = row["listing_start"]
                le: datetime = row["listing_end"]
                if mode == "on_or_after":
                    if ls < cutoff:
                        continue
                elif mode == "on_date":
                    if ls > target or le < target:
                        continue
                else:
                    if le < range_start or ls > range_end:
                        continue
                seen_urls.add(url)
                results.append(_row_from_parsed(row))
            page_num += 1
            if page_num > max_page:
                break
            if request_delay_s > 0:
                time.sleep(request_delay_s)
            html = fetch_page(client, BASE_URL, page=page_num)
    finally:
        if own_client:
            client.close()

    return results
