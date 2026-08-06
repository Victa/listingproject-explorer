"""
Local Listings Project search UI. Fetches the NYC index from the server (no CORS).

Run: streamlit run app.py
"""

from __future__ import annotations

import calendar
import concurrent.futures
import hashlib
import html as html_lib
import json
import pickle
import re
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Callable, Literal

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from listing_scraper import (
    BoroughKey,
    ListingRow,
    fetch_all_listings,
    fetch_listing_photo_urls,
)
from styles import inject_theme

FILTERS_FILE = Path(__file__).parent / ".listings_filters.json"
AUTH_FILE = Path(__file__).parent / ".listings_auth.json"
SEEN_FILE = Path(__file__).parent / ".listings_seen.json"
LISTINGS_CACHE_FILE = Path(__file__).parent / ".listings_cache.pkl"
LISTINGS_CACHE_TTL = timedelta(hours=6)

DateFilterMode = Literal["none", "dates", "flexible"]
FlexibleStay = Literal["week", "month"]

BOROUGH_LABELS: list[tuple[str, BoroughKey]] = [
    ("Manhattan", "manhattan"),
    ("Brooklyn", "brooklyn"),
    ("Queens", "queens"),
    ("Bronx", "bronx"),
    ("Staten Island", "staten_island"),
]

ALL_BOROUGH_KEYS: frozenset[BoroughKey] = frozenset(k for _, k in BOROUGH_LABELS)
VALID_DATE_MODES: frozenset[DateFilterMode] = frozenset(("none", "dates", "flexible"))
VALID_FLEXIBLE_STAYS: frozenset[FlexibleStay] = frozenset(("week", "month"))
VALID_TOLERANCE_DAYS: frozenset[int] = frozenset((0, 1, 2, 3, 7, 14))
DEFAULT_DATES_START = date(2026, 7, 1)
DEFAULT_DATES_END = date(2026, 7, 14)
FLEXIBLE_MONTH_COUNT = 12
WEEK_STAY_DAYS = 7
RESULTS_PAGE_SIZE = 15
GALLERY_PREFETCH_WORKERS = 6


def _default_filters() -> dict[str, Any]:
    return {
        "borough_keys": [],
        "neighborhoods": [],
        "property_types": [],
        "first_access_only": False,
        "new_only": False,
        "date_mode": "none",
        "dates_start": DEFAULT_DATES_START.isoformat(),
        "dates_end": DEFAULT_DATES_END.isoformat(),
        "dates_tolerance_days": 0,
        "flexible_stay": "week",
        "flexible_months": [],
    }


def _borough_widget_key(borough_key: BoroughKey) -> str:
    return f"borough_{borough_key}"


def _hood_widget_key(name: str) -> str:
    return f"hood_{name}"


def _property_type_widget_key(name: str) -> str:
    return f"ptype_{name}"


def _filter_dropdown_label(group_name: str, selected: list[str]) -> str:
    if not selected:
        return group_name
    if len(selected) == 1:
        return selected[0]
    if len(selected) <= 2:
        return ", ".join(selected)
    return f"{group_name} ({len(selected)})"

def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _tolerance_label(days: int) -> str:
    if days == 0:
        return "Exact dates"
    return f"± {days} day{'s' if days != 1 else ''}"


def _month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _parse_month_key(key: str) -> tuple[int, int] | None:
    try:
        year_s, month_s = key.split("-", 1)
        year, month = int(year_s), int(month_s)
        if month < 1 or month > 12:
            return None
        return year, month
    except (TypeError, ValueError):
        return None


def _month_bounds(month_key: str) -> tuple[date, date] | None:
    parsed = _parse_month_key(month_key)
    if parsed is None:
        return None
    year, month = parsed
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _flexible_month_options(count: int = FLEXIBLE_MONTH_COUNT) -> list[tuple[str, str]]:
    """Return ``(label, YYYY-MM key)`` pairs starting from the current month."""
    options: list[tuple[str, str]] = []
    cursor = date.today().replace(day=1)
    for _ in range(count):
        key = _month_key(cursor.year, cursor.month)
        label = cursor.strftime("%B %Y")
        options.append((label, key))
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return options


def _expand_date_range(
    range_start: date, range_end: date, tolerance_days: int
) -> tuple[date, date]:
    delta = timedelta(days=tolerance_days)
    return range_start - delta, range_end + delta


def _plural(n: int, unit: str) -> str:
    return f"{n} {unit}" if n == 1 else f"{n} {unit}s"


def _format_stay_length(start: datetime, end: datetime) -> str:
    """Human-readable inclusive stay length (e.g. '1 year', '3 months', '2 weeks')."""
    start_d = start.date()
    # Inclusive end: Sep 1 -> Aug 31 next year is exactly 1 year.
    effective_end = end.date() + timedelta(days=1)
    total_days = (effective_end - start_d).days
    if total_days <= 0:
        return ""

    months = (effective_end.year - start_d.year) * 12 + (effective_end.month - start_d.month)
    if effective_end.day < start_d.day:
        months -= 1

    if months >= 12:
        years, rem_months = divmod(months, 12)
        if rem_months:
            return f"{_plural(years, 'year')}, {_plural(rem_months, 'month')}"
        return _plural(years, "year")
    if months >= 1:
        return _plural(months, "month")
    if total_days >= 7:
        weeks = total_days // 7
        return _plural(weeks, "week")
    return _plural(total_days, "day")


def _format_short_date(value: datetime | date) -> str:
    """Abbreviated month date, e.g. 'Aug 6, 2026'."""
    d = value.date() if isinstance(value, datetime) else value
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def _format_card_date_range(start: datetime, end: datetime) -> str:
    """Compact range, e.g. 'Oct 15 to Aug 31, 2027' (omit start year when same)."""
    s = start.date() if isinstance(start, datetime) else start
    e = end.date() if isinstance(end, datetime) else end
    if s.year == e.year:
        return f"{s.strftime('%b')} {s.day} to {e.strftime('%b')} {e.day}, {e.year}"
    return f"{_format_short_date(s)} to {_format_short_date(e)}"


def _format_card_dates_html(row: ListingRow) -> str:
    """Compact date range • stay length, with length in the trailing span."""
    range_html = html_lib.escape(
        _format_card_date_range(row.listing_start, row.listing_end)
    )
    length = _format_stay_length(row.listing_start, row.listing_end)
    if not length:
        return range_html
    return (
        f"{range_html}"
        f'<span class="lp-card-dates-sep"> • </span>'
        f'<span class="lp-card-dates-length">{html_lib.escape(length)}</span>'
    )


def _should_show_listing_type(listing_type: str) -> bool:
    """True for specialty types (rooms, art studios, …); hide apartment/house."""
    t = (listing_type or "").strip().lower()
    if not t:
        return False
    if "apartment" in t or "house" in t:
        return False
    return True


def _listing_covers_interval(
    listing_start: datetime, listing_end: datetime, interval_start: date, interval_end: date
) -> bool:
    """True when the listing is available for the entire check-in through check-out window."""
    ls = listing_start.date()
    le = listing_end.date()
    return ls <= interval_start and le >= interval_end


def _listing_covers_month_stay(
    listing_start: datetime, listing_end: datetime, month_key: str
) -> bool:
    """True when the listing spans the entire selected month (a full month stay fits)."""
    bounds = _month_bounds(month_key)
    if bounds is None:
        return False
    month_start, month_end = bounds
    return listing_start.date() <= month_start and listing_end.date() >= month_end


def _listing_covers_week_stay(
    listing_start: datetime, listing_end: datetime, month_key: str
) -> bool:
    """True when the listing offers a continuous ``WEEK_STAY_DAYS`` window inside the month."""
    bounds = _month_bounds(month_key)
    if bounds is None:
        return False
    month_start, month_end = bounds
    overlap_start = max(listing_start.date(), month_start)
    overlap_end = min(listing_end.date(), month_end)
    if overlap_end < overlap_start:
        return False
    return (overlap_end - overlap_start).days + 1 >= WEEK_STAY_DAYS


def _flexible_stay_matcher(
    flexible_stay: FlexibleStay,
) -> Callable[[datetime, datetime, str], bool]:
    return (
        _listing_covers_month_stay
        if flexible_stay == "month"
        else _listing_covers_week_stay
    )


def _migrate_legacy_date_mode(raw: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    """Map persisted v1 date fields onto the new dates/flexible model."""
    date_mode = raw.get("date_mode", "none")
    if date_mode == "on_date":
        on_date = _parse_date(raw.get("on_date")) or _parse_date(defaults["dates_start"])
        if on_date is None:
            on_date = date.today()
        iso = on_date.isoformat()
        return {
            "date_mode": "dates",
            "dates_start": iso,
            "dates_end": iso,
            "dates_tolerance_days": 0,
            "flexible_stay": defaults["flexible_stay"],
            "flexible_months": [],
        }
    if date_mode == "overlap":
        range_start = _parse_date(raw.get("range_start")) or _parse_date(
            defaults["dates_start"]
        )
        range_end = _parse_date(raw.get("range_end")) or _parse_date(defaults["dates_end"])
        if range_start is None:
            range_start = DEFAULT_DATES_START
        if range_end is None:
            range_end = DEFAULT_DATES_END
        return {
            "date_mode": "dates",
            "dates_start": range_start.isoformat(),
            "dates_end": range_end.isoformat(),
            "dates_tolerance_days": 0,
            "flexible_stay": defaults["flexible_stay"],
            "flexible_months": [],
        }
    return {
        "date_mode": date_mode,
        "dates_start": raw.get("dates_start") or defaults["dates_start"],
        "dates_end": raw.get("dates_end") or defaults["dates_end"],
        "dates_tolerance_days": raw.get("dates_tolerance_days", defaults["dates_tolerance_days"]),
        "flexible_stay": raw.get("flexible_stay", defaults["flexible_stay"]),
        "flexible_months": raw.get("flexible_months", defaults["flexible_months"]),
    }


def _validate_filters(raw: dict[str, Any]) -> dict[str, Any]:
    defaults = _default_filters()
    borough_keys = [
        k for k in raw.get("borough_keys", []) if k in ALL_BOROUGH_KEYS
    ]
    neighborhoods = [n for n in raw.get("neighborhoods", []) if isinstance(n, str)]
    property_types = [t for t in raw.get("property_types", []) if isinstance(t, str)]
    migrated = _migrate_legacy_date_mode(raw, defaults)
    date_mode = migrated["date_mode"]
    if date_mode not in VALID_DATE_MODES:
        date_mode = "none"
    tolerance = migrated["dates_tolerance_days"]
    if tolerance not in VALID_TOLERANCE_DAYS:
        tolerance = defaults["dates_tolerance_days"]
    flexible_stay = migrated["flexible_stay"]
    if flexible_stay not in VALID_FLEXIBLE_STAYS:
        flexible_stay = defaults["flexible_stay"]
    valid_month_keys = {key for _, key in _flexible_month_options()}
    flexible_months = [
        m for m in migrated.get("flexible_months", []) if m in valid_month_keys
    ]
    return {
        "borough_keys": borough_keys,
        "neighborhoods": neighborhoods,
        "property_types": property_types,
        "first_access_only": bool(raw.get("first_access_only", False)),
        "new_only": bool(raw.get("new_only", False)),
        "date_mode": date_mode,
        "dates_start": migrated["dates_start"] or defaults["dates_start"],
        "dates_end": migrated["dates_end"] or defaults["dates_end"],
        "dates_tolerance_days": tolerance,
        "flexible_stay": flexible_stay,
        "flexible_months": flexible_months,
    }


def _load_persisted_filters() -> dict[str, Any] | None:
    if not FILTERS_FILE.exists():
        return None
    try:
        raw = json.loads(FILTERS_FILE.read_text(encoding="utf-8"))
        return _validate_filters(raw)
    except (json.JSONDecodeError, TypeError, OSError):
        return None


def _save_persisted_filters(filters: dict[str, Any]) -> None:
    FILTERS_FILE.write_text(json.dumps(filters, indent=2), encoding="utf-8")


def _clear_persisted_filters() -> None:
    if FILTERS_FILE.exists():
        FILTERS_FILE.unlink()


def _init_filters() -> None:
    if st.session_state.get("_filters_initialized"):
        return

    loaded = _load_persisted_filters()
    st.session_state.filters = loaded if loaded is not None else _default_filters()
    st.session_state._filters_initialized = True
    st.session_state._apply_filters_to_widgets = True


def _sync_widget_keys_from_filters(
    filters: dict[str, Any],
    hood_options: list[str],
    property_type_options: list[str],
) -> None:
    selected_boroughs = set(filters["borough_keys"])
    selected_hoods = set(filters["neighborhoods"])
    selected_property_types = set(filters.get("property_types", []))
    for _, borough_key in BOROUGH_LABELS:
        st.session_state[_borough_widget_key(borough_key)] = (
            borough_key in selected_boroughs
        )
    for hood in hood_options:
        st.session_state[_hood_widget_key(hood)] = hood in selected_hoods
    st.session_state["date_mode"] = filters["date_mode"]
    st.session_state["dates_start"] = (
        _parse_date(filters["dates_start"]) or DEFAULT_DATES_START
    )
    st.session_state["dates_end"] = (
        _parse_date(filters["dates_end"]) or DEFAULT_DATES_END
    )
    st.session_state["dates_tolerance_days"] = filters["dates_tolerance_days"]
    st.session_state["flexible_stay"] = filters["flexible_stay"]
    st.session_state["flexible_months"] = list(filters["flexible_months"])
    st.session_state["first_access_only"] = bool(filters.get("first_access_only", False))
    st.session_state["new_only"] = bool(filters.get("new_only", False))
    for ptype in property_type_options:
        st.session_state[_property_type_widget_key(ptype)] = (
            ptype in selected_property_types
        )


def _collect_filters_from_widgets(
    hood_options: list[str], property_type_options: list[str]
) -> dict[str, Any]:
    borough_keys = [
        borough_key
        for _, borough_key in BOROUGH_LABELS
        if st.session_state.get(_borough_widget_key(borough_key), False)
    ]
    neighborhoods = [
        hood
        for hood in hood_options
        if st.session_state.get(_hood_widget_key(hood), False)
    ]
    date_mode: DateFilterMode = st.session_state.get("date_mode", "none")
    property_types = [
        ptype
        for ptype in property_type_options
        if st.session_state.get(_property_type_widget_key(ptype), False)
    ]
    filters: dict[str, Any] = {
        "borough_keys": borough_keys,
        "neighborhoods": neighborhoods,
        "property_types": property_types,
        "first_access_only": bool(st.session_state.get("first_access_only", False)),
        "new_only": bool(st.session_state.get("new_only", False)),
        "date_mode": date_mode,
        "dates_start": DEFAULT_DATES_START.isoformat(),
        "dates_end": DEFAULT_DATES_END.isoformat(),
        "dates_tolerance_days": 0,
        "flexible_stay": "week",
        "flexible_months": [],
    }
    if date_mode == "dates":
        dates_start = st.session_state.get("dates_start")
        dates_end = st.session_state.get("dates_end")
        tolerance = st.session_state.get("dates_tolerance_days", 0)
        if isinstance(dates_start, date):
            filters["dates_start"] = dates_start.isoformat()
        if isinstance(dates_end, date):
            filters["dates_end"] = dates_end.isoformat()
        if tolerance in VALID_TOLERANCE_DAYS:
            filters["dates_tolerance_days"] = tolerance
    elif date_mode == "flexible":
        flexible_stay = st.session_state.get("flexible_stay", "week")
        if flexible_stay in VALID_FLEXIBLE_STAYS:
            filters["flexible_stay"] = flexible_stay
        month_selection = st.session_state.get("flexible_months", [])
        if isinstance(month_selection, list):
            valid_keys = {key for _, key in _flexible_month_options()}
            filters["flexible_months"] = [m for m in month_selection if m in valid_keys]
    return filters


def _large_photo_url(url: str | None) -> str | None:
    """Bump Bunny CDN sizing so thumbnails stay sharp when shown taller."""
    if not url:
        return None
    bumped = re.sub(r"(?<=[?&])width=\d+", "width=720", url)
    bumped = re.sub(r"(?<=[?&])height=\d+", "height=720", bumped)
    if "width=" not in bumped and "height=" not in bumped:
        sep = "&" if "?" in bumped else "?"
        bumped = f"{bumped}{sep}width=720"
    return bumped


@st.cache_data(show_spinner=False)
def load_listing_photos(url: str, cookie: str | None = None) -> tuple[str, ...]:
    """Cached detail-page gallery URLs for a listing (empty if fetch/parse fails)."""
    try:
        return fetch_listing_photo_urls(url, cookie=cookie)
    except Exception:
        return ()


def _gallery_session_key(url: str) -> str:
    return f"_gallery_urls_{_card_key_id(url)}"


def _prefetch_page_galleries(
    rows: list[ListingRow],
    cookie: str | None = None,
) -> None:
    """Fetch detail galleries for page rows missing from session (parallel, cached)."""
    pending = [row for row in rows if _gallery_session_key(row.url) not in st.session_state]
    if not pending:
        return

    def _fetch_one(row: ListingRow) -> tuple[str, tuple[str, ...]]:
        fetched = load_listing_photos(row.url, cookie)
        if fetched:
            return row.url, fetched
        fallback: tuple[str, ...] = (row.photo_url,) if row.photo_url else ()
        return row.url, fallback

    with st.spinner("Loading photos…"):
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=GALLERY_PREFETCH_WORKERS
        ) as executor:
            results = list(executor.map(_fetch_one, pending))

    for url, gallery in results:
        st.session_state[_gallery_session_key(url)] = gallery


def _render_photo_carousel(
    urls: tuple[str, ...],
    *,
    element_id: str,
    listing_url: str = "",
    height: int = 250,
) -> None:
    """Client-side carousel (arrows + dots) — no Streamlit rerun on navigation."""
    display = [_large_photo_url(u) or u for u in urls]
    escaped = [html_lib.escape(u, quote=True) for u in display]
    urls_js = json.dumps(display)
    root_id = html_lib.escape(element_id, quote=True)
    href = html_lib.escape(listing_url, quote=True) if listing_url else ""
    img_open = (
        f'<a class="lp-carousel-link" href="{href}" target="_blank" rel="noopener noreferrer">'
        if href
        else ""
    )
    img_close = "</a>" if href else ""
    dots_html = "".join(
        f'<button type="button" class="lp-dot{" is-active" if i == 0 else ""}" '
        f'data-i="{i}" aria-label="Photo {i + 1}"></button>'
        for i in range(len(escaped))
    )
    html = f"""
<div class="lp-carousel" id="{root_id}">
  {img_open}<img class="lp-carousel-img" src="{escaped[0]}" alt="Listing photo" />{img_close}
  <div class="lp-carousel-skeleton" aria-hidden="true"></div>
  <button type="button" class="lp-nav lp-prev" aria-label="Previous photo">&#8249;</button>
  <button type="button" class="lp-nav lp-next" aria-label="Next photo">&#8250;</button>
  <div class="lp-dots">{dots_html}</div>
</div>
<style>
  html, body {{
    margin: 0;
    padding: 0;
    overflow: hidden;
    height: 100%;
  }}
  .lp-carousel {{
    position: relative;
    width: 100%;
    height: 100%;
    overflow: hidden;
    background: #e5e5ea;
    border-radius: 14px;
  }}
  .lp-carousel-link {{
    display: block;
    width: 100%;
    height: 100%;
  }}
  .lp-carousel-img {{
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
    opacity: 1;
    transition: opacity 0.18s ease;
  }}
  .lp-carousel-skeleton {{
    position: absolute;
    inset: 0;
    z-index: 1;
    pointer-events: none;
    opacity: 0;
    visibility: hidden;
    background: #e5e5ea;
    overflow: hidden;
    transition: opacity 0.12s ease, visibility 0.12s ease;
  }}
  .lp-carousel-skeleton::after {{
    content: "";
    position: absolute;
    inset: 0;
    background: linear-gradient(
      90deg,
      transparent 0%,
      rgba(255, 255, 255, 0.55) 50%,
      transparent 100%
    );
    transform: translateX(-100%);
    animation: lp-shimmer 1.1s ease-in-out infinite;
  }}
  .lp-carousel.is-loading .lp-carousel-skeleton {{
    opacity: 1;
    visibility: visible;
  }}
  .lp-carousel.is-loading .lp-carousel-img {{
    opacity: 0;
  }}
  @keyframes lp-shimmer {{
    100% {{ transform: translateX(100%); }}
  }}
  .lp-nav {{
    position: absolute;
    top: 50%;
    transform: translateY(-50%);
    width: 28px;
    height: 28px;
    border: none;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.88);
    color: #1d1d1f;
    font-size: 20px;
    line-height: 1;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 1px 4px rgba(0, 0, 0, 0.12);
    padding: 0;
    z-index: 2;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.15s ease;
  }}
  .lp-prev {{ left: 8px; }}
  .lp-next {{ right: 8px; }}
  .lp-dots {{
    position: absolute;
    left: 0;
    right: 0;
    bottom: 8px;
    display: flex;
    justify-content: center;
    gap: 5px;
    pointer-events: none;
    z-index: 2;
    opacity: 0;
    transition: opacity 0.15s ease;
  }}
  .lp-carousel:hover .lp-nav,
  .lp-carousel:focus-within .lp-nav {{
    opacity: 1;
    pointer-events: auto;
  }}
  .lp-carousel:hover .lp-dots,
  .lp-carousel:focus-within .lp-dots {{
    opacity: 1;
  }}
  .lp-dot {{
    pointer-events: auto;
    width: 6px;
    height: 6px;
    border-radius: 999px;
    border: none;
    padding: 0;
    background: rgba(255, 255, 255, 0.55);
    cursor: pointer;
  }}
  .lp-dot.is-active {{
    background: #fff;
  }}
</style>
<script>
(function() {{
  const urls = {urls_js};
  const root = document.getElementById("{root_id}");
  if (!root || urls.length < 2) return;
  const img = root.querySelector(".lp-carousel-img");
  const dots = Array.from(root.querySelectorAll(".lp-dot"));
  const ready = new Set();
  let i = 0;
  let loadGen = 0;

  function markReady(url) {{
    ready.add(url);
  }}

  function prefetch(url) {{
    if (!url || ready.has(url)) return;
    const pre = new Image();
    pre.onload = () => markReady(url);
    pre.onerror = () => markReady(url);
    pre.src = url;
  }}

  function prefetchNeighbors() {{
    prefetch(urls[(i + 1) % urls.length]);
    prefetch(urls[(i - 1 + urls.length) % urls.length]);
  }}

  function finishShow(url, gen) {{
    if (gen !== loadGen) return;
    markReady(url);
    root.classList.remove("is-loading");
    img.style.opacity = "1";
    prefetchNeighbors();
  }}

  function show(n) {{
    i = (n + urls.length) % urls.length;
    dots.forEach((d, idx) => d.classList.toggle("is-active", idx === i));
    const url = urls[i];
    const gen = ++loadGen;

    if (ready.has(url)) {{
      root.classList.remove("is-loading");
      img.style.opacity = "1";
      img.onload = null;
      img.onerror = null;
      img.src = url;
      prefetchNeighbors();
      return;
    }}

    root.classList.add("is-loading");
    img.style.opacity = "0";
    img.onload = () => finishShow(url, gen);
    img.onerror = () => finishShow(url, gen);
    img.src = url;
    if (img.complete) finishShow(url, gen);
  }}

  if (img.complete) markReady(urls[0]);
  else {{
    img.onload = () => {{
      markReady(urls[0]);
      prefetchNeighbors();
    }};
    img.onerror = () => markReady(urls[0]);
  }}
  prefetchNeighbors();

  root.querySelector(".lp-prev").addEventListener("click", (e) => {{
    e.preventDefault();
    e.stopPropagation();
    show(i - 1);
  }});
  root.querySelector(".lp-next").addEventListener("click", (e) => {{
    e.preventDefault();
    e.stopPropagation();
    show(i + 1);
  }});
  dots.forEach((d) => d.addEventListener("click", (e) => {{
    e.preventDefault();
    e.stopPropagation();
    show(+d.dataset.i);
  }}));
}})();
</script>
"""
    components.html(html, height=height)


def _load_auth_cookie() -> str | None:
    """Return cookie header from ``.listings_auth.json``, or None if unset/invalid."""
    if not AUTH_FILE.exists():
        return None
    try:
        raw = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    cookie = raw.get("cookie_header")
    if isinstance(cookie, str) and cookie.strip():
        return cookie.strip()
    return None


def _load_seen_urls() -> set[str]:
    """Return previously-seen listing URLs from ``.listings_seen.json``."""
    if not SEEN_FILE.exists():
        return set()
    try:
        raw = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    if not isinstance(raw, dict):
        return set()
    urls = raw.get("seen_urls", [])
    if not isinstance(urls, list):
        return set()
    return {u for u in urls if isinstance(u, str) and u}


def _save_seen_urls(urls: set[str]) -> None:
    SEEN_FILE.write_text(
        json.dumps({"seen_urls": sorted(urls)}, indent=2),
        encoding="utf-8",
    )


def _cookie_cache_key(cookie: str | None) -> str:
    return hashlib.sha256((cookie or "").encode("utf-8")).hexdigest()[:16]


def _read_listings_disk_cache(cookie: str | None) -> list[ListingRow] | None:
    if not LISTINGS_CACHE_FILE.exists():
        return None
    try:
        with LISTINGS_CACHE_FILE.open("rb") as f:
            payload = pickle.load(f)
    except (OSError, pickle.UnpicklingError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    fetched_at = payload.get("fetched_at")
    rows = payload.get("rows")
    if not isinstance(fetched_at, datetime) or not isinstance(rows, list):
        return None
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    if payload.get("cookie_key") != _cookie_cache_key(cookie):
        return None
    if datetime.now(timezone.utc) - fetched_at > LISTINGS_CACHE_TTL:
        return None
    return rows


def _write_listings_disk_cache(rows: list[ListingRow], cookie: str | None) -> None:
    payload = {
        "fetched_at": datetime.now(timezone.utc),
        "cookie_key": _cookie_cache_key(cookie),
        "rows": rows,
    }
    LISTINGS_CACHE_FILE.write_bytes(pickle.dumps(payload))


def _clear_listings_disk_cache() -> None:
    if LISTINGS_CACHE_FILE.exists():
        LISTINGS_CACHE_FILE.unlink()


def _listing_urls_fingerprint(rows: list[ListingRow]) -> frozenset[str]:
    return frozenset(row.url for row in rows)


@st.cache_data(show_spinner=False, ttl=LISTINGS_CACHE_TTL)
def load_listings(cookie: str | None = None) -> list[ListingRow]:
    cached = _read_listings_disk_cache(cookie)
    if cached is not None:
        return cached

    progress_ph = st.empty()

    def on_progress(page: int, total: int) -> None:
        progress_ph.progress(
            page / max(total, 1),
            text=f"Fetching listings — page {page} of {total}…",
        )

    try:
        rows = fetch_all_listings(progress=on_progress, cookie=cookie)
    finally:
        progress_ph.empty()

    _write_listings_disk_cache(rows, cookie)
    return rows


def build_neighborhood_map(rows: list[ListingRow]) -> dict[BoroughKey, list[str]]:
    buckets: dict[BoroughKey, set[str]] = {}
    for row in rows:
        if row.borough_key == "all":
            continue
        buckets.setdefault(row.borough_key, set()).update(row.neighborhood_names)
    return {key: sorted(names) for key, names in buckets.items()}


def build_property_type_options(rows: list[ListingRow]) -> list[str]:
    types: set[str] = set()
    for row in rows:
        if row.listing_type:
            types.add(row.listing_type)
    return sorted(types)


def neighborhood_options_for_boroughs(
    borough_keys: set[BoroughKey],
    neighborhood_map: dict[BoroughKey, list[str]],
) -> list[str]:
    if not borough_keys:
        all_names: set[str] = set()
        for names in neighborhood_map.values():
            all_names.update(names)
        return sorted(all_names)
    all_names: set[str] = set()
    for key in borough_keys:
        all_names.update(neighborhood_map.get(key, []))
    return sorted(all_names)


def filter_rows(
    rows: list[ListingRow],
    *,
    borough_keys: set[BoroughKey],
    neighborhoods: set[str],
    property_types: set[str],
    first_access_only: bool,
    new_only: bool,
    new_urls: set[str],
    date_mode: DateFilterMode,
    dates_start: date | None,
    dates_end: date | None,
    dates_tolerance_days: int,
    flexible_stay: FlexibleStay,
    flexible_months: list[str],
) -> list[ListingRow]:
    filtered: list[ListingRow] = []
    expanded_start: date | None = None
    expanded_end: date | None = None
    if date_mode == "dates" and dates_start and dates_end:
        expanded_start, expanded_end = _expand_date_range(
            dates_start, dates_end, dates_tolerance_days
        )

    for row in rows:
        if first_access_only and not row.is_first_access:
            continue
        if new_only and row.url not in new_urls:
            continue
        if borough_keys and row.borough_key not in borough_keys:
            continue
        if neighborhoods and not (neighborhoods & set(row.neighborhood_names)):
            continue
        if property_types and row.listing_type not in property_types:
            continue
        if date_mode == "dates":
            if expanded_start is None or expanded_end is None:
                continue
            if not _listing_covers_interval(
                row.listing_start, row.listing_end, expanded_start, expanded_end
            ):
                continue
        elif date_mode == "flexible":
            if not flexible_months:
                continue
            matches_stay = _flexible_stay_matcher(flexible_stay)
            if not any(
                matches_stay(row.listing_start, row.listing_end, month_key)
                for month_key in flexible_months
            ):
                continue
        filtered.append(row)
    return filtered


def _card_key_id(url: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:12]


def _pagination_page_numbers(page: int, total_pages: int) -> list[int | None]:
    """Visible page numbers for the pager; ``None`` is an ellipsis gap."""
    if total_pages <= 7:
        return list(range(1, total_pages + 1))

    pages: set[int] = {1, total_pages}
    for p in (page - 1, page, page + 1):
        if 1 <= p <= total_pages:
            pages.add(p)
    if page <= 3:
        pages.update(range(1, 5))
    if page >= total_pages - 2:
        pages.update(range(total_pages - 3, total_pages + 1))

    ordered = sorted(pages)
    items: list[int | None] = []
    prev = 0
    for p in ordered:
        if prev and p - prev > 1:
            items.append(None)
        items.append(p)
        prev = p
    return items


def _render_results_pagination(page: int, total_pages: int) -> None:
    """Numbered circle pagination (‹ 1 2 3 … N ›)."""
    if total_pages <= 1:
        return

    def _go_to_page(target: int) -> None:
        st.session_state.results_page = target
        st.session_state._scroll_results_top = True
        st.rerun()

    slots: list[tuple[str, int | None]] = [("prev", None)]
    for item in _pagination_page_numbers(page, total_pages):
        if item is None:
            slots.append(("ellipsis", None))
        else:
            slots.append(("page", item))
    slots.append(("next", None))

    with st.container(key="results_pagination"):
        cols = st.columns(len(slots), gap="small")
        for col, (kind, value) in zip(cols, slots):
            with col:
                if kind == "ellipsis":
                    st.markdown(
                        '<div class="lp-page-ellipsis">…</div>',
                        unsafe_allow_html=True,
                    )
                elif kind == "prev":
                    if st.button(
                        "‹",
                        key="results_prev",
                        disabled=page <= 1,
                        type="secondary",
                    ):
                        _go_to_page(page - 1)
                elif kind == "next":
                    if st.button(
                        "›",
                        key="results_next",
                        disabled=page >= total_pages,
                        type="secondary",
                    ):
                        _go_to_page(page + 1)
                else:
                    assert value is not None
                    is_current = value == page
                    if st.button(
                        str(value),
                        key=f"results_page_{value}",
                        type="primary" if is_current else "secondary",
                        disabled=is_current,
                    ):
                        _go_to_page(value)


def _scroll_main_to_top() -> None:
    """Scroll the Streamlit main pane to the top (used after pagination)."""
    components.html(
        """
<script>
(function () {
  const doc = window.parent.document;
  const candidates = [
    doc.querySelector('[data-testid="stMain"]'),
    doc.querySelector('section.main'),
    doc.querySelector('[data-testid="stAppViewContainer"]'),
    doc.documentElement,
  ];
  for (const el of candidates) {
    if (el && typeof el.scrollTo === "function") {
      el.scrollTo({ top: 0, left: 0, behavior: "instant" });
    }
  }
  window.parent.scrollTo(0, 0);
})();
</script>
        """,
        height=0,
    )


_PRICE_RE = re.compile(
    r"^(\$\s*[0-9][0-9,]*)\s*(?:/\s*([A-Za-z]+)|(?:\s+(monthly|month|weekly|week|daily|day|nightly|night)))?\s*$",
    re.IGNORECASE,
)


def _normalize_price_period(suffix: str) -> str:
    """Map scraped price suffix to a canonical period label."""
    s = (suffix or "").lower()
    if s in ("", "mo", "month", "monthly"):
        return "monthly"
    if s in ("wk", "week", "weekly"):
        return "weekly"
    if s in ("day", "daily"):
        return "daily"
    if s in ("night", "nightly"):
        return "nightly"
    return s


def _parse_price(price: str) -> tuple[int, str] | None:
    """Parse scraped price into (amount_dollars, period) or None if unparseable."""
    raw = (price or "").strip()
    if not raw or raw == "N/A":
        return None
    m = _PRICE_RE.match(raw)
    if not m:
        return None
    digits = re.sub(r"[^\d]", "", m.group(1))
    if not digits:
        return None
    period = _normalize_price_period(m.group(2) or m.group(3) or "")
    return int(digits), period


def _listing_stay_days(start: datetime, end: datetime) -> int:
    """Inclusive availability length in days."""
    return (end.date() - start.date()).days + 1


def _display_price_for_row(row: ListingRow) -> str:
    """Price string for cards; convert daily/nightly/weekly to monthly when stay > 30 days."""
    parsed = _parse_price(row.price)
    if parsed is None:
        return row.price
    amount, period = parsed
    if _listing_stay_days(row.listing_start, row.listing_end) <= 30:
        return row.price
    if period in ("daily", "nightly"):
        monthly = amount * 30
    elif period == "weekly":
        monthly = amount * 4
    else:
        return row.price
    return f"${monthly:,}/mo"


def _format_price_html(price: str) -> str:
    """Bold underlined amount + muted period label (e.g. monthly)."""
    raw = (price or "").strip()
    if not raw or raw == "N/A":
        return '<span class="lp-price-amount">N/A</span>'

    m = _PRICE_RE.match(raw)
    if not m:
        esc = html_lib.escape(raw)
        return f'<span class="lp-price-amount">{esc}</span>'

    amount = html_lib.escape(re.sub(r"\s+", "", m.group(1)))
    period = _normalize_price_period(m.group(2) or m.group(3) or "")

    return (
        f'<span class="lp-price-amount">{amount}</span>'
        f' <span class="lp-price-period">{html_lib.escape(period)}</span>'
    )


def _render_single_photo(
    photo_url: str,
    *,
    listing_url: str,
    height: int = 250,
) -> None:
    """Static cover image linked to the listing detail page."""
    src = html_lib.escape(_large_photo_url(photo_url) or photo_url, quote=True)
    href = html_lib.escape(listing_url, quote=True)
    html = f"""
<a class="lp-photo-link" href="{href}" target="_blank" rel="noopener noreferrer">
  <img class="lp-photo-img" src="{src}" alt="Listing photo" />
</a>
<style>
  html, body {{
    margin: 0;
    padding: 0;
    overflow: hidden;
    height: 100%;
  }}
  .lp-photo-link {{
    display: block;
    width: 100%;
    height: 100%;
    overflow: hidden;
    background: #e5e5ea;
    border-radius: 14px;
  }}
  .lp-photo-img {{
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
  }}
</style>
"""
    components.html(html, height=height)


def render_listing_card(row: ListingRow, *, is_new: bool) -> None:
    card_key = f"{'new_card_' if is_new else 'seen_card_'}{_card_key_id(row.url)}"
    gallery_key = _gallery_session_key(row.url)
    url_esc = html_lib.escape(row.url, quote=True)
    with st.container(border=False, key=card_key):
        index_photo = _large_photo_url(row.photo_url)
        gallery: tuple[str, ...] | None = st.session_state.get(gallery_key)

        if gallery is not None and len(gallery) > 1:
            _render_photo_carousel(
                gallery,
                element_id=f"lp-c-{card_key}",
                listing_url=row.url,
            )
        elif gallery:
            one = gallery[0]
            if one:
                _render_single_photo(one, listing_url=row.url)
            elif index_photo:
                _render_single_photo(index_photo, listing_url=row.url)
            else:
                st.html('<div class="lp-photo-placeholder"></div>')
        elif index_photo:
            _render_single_photo(index_photo, listing_url=row.url)
        else:
            st.html('<div class="lp-photo-placeholder"></div>')

        badge_html = ""
        if is_new:
            badge_html += '<span class="lp-badge lp-badge-new">New</span>'
        if row.is_first_access:
            badge_html += '<span class="lp-badge lp-badge-access">First Access</span>'

        title_esc = html_lib.escape(row.title)
        if row.neighborhood_names:
            hood_label = ", ".join(row.neighborhood_names)
        else:
            hood_label = row.neighborhood_name or row.neighborhood
        location = (
            f"{hood_label}, {row.borough_label}"
            if row.borough_label
            else hood_label
        )
        location_esc = html_lib.escape(location)
        dates_html = _format_card_dates_html(row)
        price_html = _format_price_html(_display_price_for_row(row))
        type_html = ""
        if _should_show_listing_type(row.listing_type):
            type_html = (
                f'<div class="lp-card-meta">'
                f"{html_lib.escape(row.listing_type)}"
                f"</div>"
            )

        # st.html avoids Streamlit markdown/KaTeX rewriting titles like "$1,000..." or "*Sale*".
        st.html(
            (
                f'<a class="lp-card-hit" href="{url_esc}" target="_blank" '
                f'rel="noopener noreferrer" aria-label="{title_esc}"></a>'
                f'<div class="lp-card-body">'
                f"{type_html}"
                f'<div class="lp-card-title">{title_esc}</div>'
                f'<div class="lp-card-meta">{location_esc}</div>'
                f'<div class="lp-card-dates">{dates_html}</div>'
                f'<div class="lp-card-price">{price_html}</div>'
                f'{f'<div class="lp-card-badges">{badge_html}</div>' if badge_html else ""}'
                f'</div>'
            ),
        )


st.set_page_config(page_title="Listings Project search", layout="wide")
inject_theme()
st.title("Listings Project — NYC search")
st.caption(
    "Browse the NYC index with borough, neighborhood, property type, and availability filters."
)

_init_filters()

auth_cookie = _load_auth_cookie()

# Clear cache and continue this run (no early rerun) so filter widgets still
# render and Streamlit does not wipe their session_state keys.
if st.sidebar.button("Refresh listings"):
    load_listings.clear()
    _clear_listings_disk_cache()
    st.session_state.pop("_seen_initialized", None)
    st.session_state._apply_filters_to_widgets = True

try:
    with st.spinner("Loading listings from listingsproject.com…"):
        all_rows = load_listings(cookie=auth_cookie)
except Exception as e:
    st.error(f"Request failed: {e}")
    st.stop()

listing_fp = _listing_urls_fingerprint(all_rows)
if (
    not st.session_state.get("_seen_initialized")
    or st.session_state.get("_listing_urls_fp") != listing_fp
):
    seen = _load_seen_urls()
    current = set(listing_fp)
    st.session_state._new_urls = current - seen
    _save_seen_urls(seen | current)
    st.session_state._listing_urls_fp = listing_fp
    st.session_state._seen_initialized = True

if auth_cookie and not any(r.is_first_access for r in all_rows):
    st.sidebar.warning(
        "First-access session expired — update `.listings_auth.json` with a fresh cookie."
    )
neighborhood_map = build_neighborhood_map(all_rows)
property_type_options = build_property_type_options(all_rows)
filters = st.session_state.filters

if st.session_state.pop("_apply_filters_to_widgets", False):
    hood_for_sync = neighborhood_options_for_boroughs(
        set(filters["borough_keys"]), neighborhood_map
    )
    _sync_widget_keys_from_filters(filters, hood_for_sync, property_type_options)

with st.sidebar:
    st.header("Filters")

    st.checkbox("First access only", key="first_access_only")
    st.checkbox("New only", key="new_only")

    selected_borough_labels = [
        label
        for label, borough_key in BOROUGH_LABELS
        if st.session_state.get(_borough_widget_key(borough_key), False)
    ]
    with st.popover(
        _filter_dropdown_label("Borough", selected_borough_labels),
        use_container_width=True,
    ):
        for label, borough_key in BOROUGH_LABELS:
            st.checkbox(label, key=_borough_widget_key(borough_key))

    selected_borough_keys = {
        borough_key
        for _, borough_key in BOROUGH_LABELS
        if st.session_state.get(_borough_widget_key(borough_key), False)
    }
    hood_options = neighborhood_options_for_boroughs(
        selected_borough_keys, neighborhood_map
    )

    selected_hood_labels = [
        hood
        for hood in hood_options
        if st.session_state.get(_hood_widget_key(hood), False)
    ]
    with st.popover(
        _filter_dropdown_label("Neighborhood", selected_hood_labels),
        use_container_width=True,
    ):
        hood_col1, hood_col2 = st.columns(2)
        with hood_col1:
            if st.button("Select all", key="hood_select_all"):
                for hood in hood_options:
                    st.session_state[_hood_widget_key(hood)] = True
                st.rerun()
        with hood_col2:
            if st.button("Clear", key="hood_clear"):
                for hood in hood_options:
                    st.session_state[_hood_widget_key(hood)] = False
                st.rerun()

        with st.container(height=300):
            for hood in hood_options:
                st.checkbox(hood, key=_hood_widget_key(hood))

    selected_property_type_labels = [
        ptype
        for ptype in property_type_options
        if st.session_state.get(_property_type_widget_key(ptype), False)
    ]
    with st.popover(
        _filter_dropdown_label("Property type", selected_property_type_labels),
        use_container_width=True,
    ):
        ptype_col1, ptype_col2 = st.columns(2)
        with ptype_col1:
            if st.button("Select all", key="ptype_select_all"):
                for ptype in property_type_options:
                    st.session_state[_property_type_widget_key(ptype)] = True
                st.rerun()
        with ptype_col2:
            if st.button("Clear", key="ptype_clear"):
                for ptype in property_type_options:
                    st.session_state[_property_type_widget_key(ptype)] = False
                st.rerun()

        with st.container(height=300):
            for ptype in property_type_options:
                st.checkbox(ptype, key=_property_type_widget_key(ptype))

    st.subheader("Date availability")
    date_mode: DateFilterMode = st.radio(
        "Availability",
        options=["none", "dates", "flexible"],
        format_func=lambda m: {
            "none": "Any dates",
            "dates": "Dates",
            "flexible": "Flexible",
        }[m],
        key="date_mode",
        horizontal=True,
    )

    dates_start: date | None = None
    dates_end: date | None = None
    dates_tolerance_days = 0
    flexible_stay: FlexibleStay = "week"
    flexible_months: list[str] = []

    if date_mode == "dates":
        picked = st.date_input(
            "Check-in — Check-out",
            value=(
                st.session_state.get("dates_start", DEFAULT_DATES_START),
                st.session_state.get("dates_end", DEFAULT_DATES_END),
            ),
        )
        if isinstance(picked, tuple) and len(picked) == 2:
            dates_start, dates_end = picked
            st.session_state["dates_start"] = dates_start
            st.session_state["dates_end"] = dates_end
        elif isinstance(picked, date):
            dates_start = dates_end = picked
            st.session_state["dates_start"] = dates_start
            st.session_state["dates_end"] = dates_end
        if dates_start and dates_end and dates_start > dates_end:
            st.error("Check-in must be on or before check-out.")
            st.stop()

        dates_tolerance_days = st.radio(
            "Flexibility",
            options=[0, 1, 2, 3, 7, 14],
            format_func=_tolerance_label,
            key="dates_tolerance_days",
            horizontal=True,
        )

    elif date_mode == "flexible":
        flexible_stay = st.radio(
            "How long would you like to stay?",
            options=["week", "month"],
            format_func=lambda s: s.capitalize(),
            key="flexible_stay",
            horizontal=True,
        )
        month_options = _flexible_month_options()
        month_labels = {key: label for label, key in month_options}
        flexible_months = st.multiselect(
            "Go anytime",
            options=[key for _, key in month_options],
            format_func=lambda key: month_labels.get(key, key),
            key="flexible_months",
        )
        if not flexible_months:
            st.caption("Select one or more months to filter by availability.")

    st.session_state.filters = _collect_filters_from_widgets(
        hood_options, property_type_options
    )
    _save_persisted_filters(st.session_state.filters)

    if st.button("Clear filters", use_container_width=True):
        st.session_state.filters = _default_filters()
        _clear_persisted_filters()
        _save_persisted_filters(st.session_state.filters)
        st.session_state._apply_filters_to_widgets = True
        st.rerun()

selected_borough_keys = {
    borough_key
    for _, borough_key in BOROUGH_LABELS
    if st.session_state.get(_borough_widget_key(borough_key), False)
}
selected_neighborhoods = {
    hood
    for hood in hood_options
    if st.session_state.get(_hood_widget_key(hood), False)
}
selected_property_types = {
    ptype
    for ptype in property_type_options
    if st.session_state.get(_property_type_widget_key(ptype), False)
}

filtered = filter_rows(
    all_rows,
    borough_keys=selected_borough_keys,
    neighborhoods=selected_neighborhoods,
    property_types=selected_property_types,
    first_access_only=bool(st.session_state.get("first_access_only", False)),
    new_only=bool(st.session_state.get("new_only", False)),
    new_urls=st.session_state.get("_new_urls", set()),
    date_mode=date_mode,
    dates_start=dates_start,
    dates_end=dates_end,
    dates_tolerance_days=dates_tolerance_days,
    flexible_stay=flexible_stay,
    flexible_months=flexible_months,
)

filter_fp = json.dumps(
    {
        "borough_keys": sorted(selected_borough_keys),
        "neighborhoods": sorted(selected_neighborhoods),
        "property_types": sorted(selected_property_types),
        "first_access_only": bool(st.session_state.get("first_access_only", False)),
        "new_only": bool(st.session_state.get("new_only", False)),
        "date_mode": date_mode,
        "dates_start": dates_start.isoformat() if dates_start else None,
        "dates_end": dates_end.isoformat() if dates_end else None,
        "dates_tolerance_days": dates_tolerance_days,
        "flexible_stay": flexible_stay,
        "flexible_months": sorted(flexible_months or []),
    },
    sort_keys=True,
)
if st.session_state.get("_results_filter_fp") != filter_fp:
    st.session_state._results_filter_fp = filter_fp
    st.session_state.results_page = 1

total_pages = max(1, (len(filtered) + RESULTS_PAGE_SIZE - 1) // RESULTS_PAGE_SIZE)
page = int(st.session_state.get("results_page", 1))
page = max(1, min(page, total_pages))
st.session_state.results_page = page
start = (page - 1) * RESULTS_PAGE_SIZE
end = min(start + RESULTS_PAGE_SIZE, len(filtered))
page_rows = filtered[start:end]

new_urls: set[str] = st.session_state.get("_new_urls", set())
new_in_filtered = sum(1 for r in filtered if r.url in new_urls)
subheader = f"{len(filtered)} listing{'s' if len(filtered) != 1 else ''}"
if new_in_filtered:
    subheader += f" · {new_in_filtered} new"
if filtered and len(filtered) > RESULTS_PAGE_SIZE:
    subheader += f" · showing {start + 1}–{end}"
st.subheader(subheader)

if st.session_state.pop("_scroll_results_top", False):
    _scroll_main_to_top()

if not filtered:
    st.info("No listings match these filters.")
else:
    _prefetch_page_galleries(page_rows, auth_cookie)
    with st.container(key="listings_grid"):
        for row in page_rows:
            render_listing_card(
                row,
                is_new=row.url in new_urls,
            )

    if total_pages > 1:
        _render_results_pagination(page, total_pages)

    df = pd.DataFrame(
        [
            {
                "title": r.title,
                "neighborhood": r.neighborhood_name,
                "canonical_neighborhoods": "; ".join(r.neighborhood_names),
                "borough": r.borough_label,
                "type": r.listing_type,
                "available_from": r.listing_start.date(),
                "available_to": r.listing_end.date(),
                "price": r.price,
                "description": r.description,
                "url": r.url,
            }
            for r in filtered
        ]
    )
    buf = StringIO()
    df.to_csv(buf, index=False)
    st.download_button(
        "Download CSV",
        data=buf.getvalue(),
        file_name="listings_results.csv",
        mime="text/csv",
    )
