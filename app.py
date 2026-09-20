"""
ListingProject Explorer: cached regional listings with background refresh.

Run: streamlit run app.py
"""

from __future__ import annotations

import calendar
import concurrent.futures
import hashlib
import html as html_lib
import json
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
    NYC_REGION,
    fetch_listing_photo_urls,
)
from listing_categories import (SPACE_TYPES, ARRANGEMENTS, POST_KINDS, classify_category, migrate_category_filters)
from checked_select import checked_select
from styles import inject_theme
from listings_store import ListingsStore, access_key

FILTERS_FILE = Path(__file__).parent / ".listings_filters.json"
AUTH_FILE = Path(__file__).parent / ".listings_auth.json"
SEEN_FILE = Path(__file__).parent / ".listings_seen.json"

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
        "region_keys": [],
        "borough_keys": [],
        "neighborhoods": [],
        "filter_version": 2,
        "space_types": [],
        "arrangements": [],
        "post_kind": "all",
        "first_access_only": False,
        "new_only": False,
        "date_mode": "none",
        "dates_start": DEFAULT_DATES_START.isoformat(),
        "dates_end": DEFAULT_DATES_END.isoformat(),
        "dates_tolerance_days": 0,
        "flexible_stay": "week",
        "flexible_months": [],
    }


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
    if row.listing_start is not None and row.listing_end is None:
        return f"From {_format_short_date(row.listing_start)} · End date not specified"
    if row.listing_start is None or row.listing_end is None:
        return "Availability not specified"
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
    raw = migrate_category_filters(raw)
    defaults = _default_filters()
    borough_keys = [
        k for k in raw.get("borough_keys", []) if k in ALL_BOROUGH_KEYS
    ]
    regions = [r for r in raw.get("region_keys", [NYC_REGION]) if isinstance(r, str)]
    neighborhoods = [n if "::" in n else f"{NYC_REGION}::{n}"
                     for n in raw.get("neighborhoods", []) if isinstance(n, str)]
    space_types = [t for t in raw.get("space_types", []) if t in SPACE_TYPES]
    arrangements = [t for t in raw.get("arrangements", []) if t in ARRANGEMENTS]
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
        "region_keys": regions,
        "borough_keys": borough_keys if regions == [NYC_REGION] else [],
        "neighborhoods": neighborhoods,
        "filter_version": 2,
        "space_types": space_types,
        "arrangements": arrangements,
        "post_kind": raw.get("post_kind") if raw.get("post_kind") in POST_KINDS else "all",
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
        if raw.get("filter_version", 0) < 2 and raw.get("property_types"):
            st.session_state._category_migration_notice = True
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
        if st.session_state.filters.get("filter_version", 0) < 2:
            st.session_state._category_migration_notice = bool(st.session_state.filters.get("property_types"))
            st.session_state.filters = _validate_filters(st.session_state.filters)
            st.session_state._apply_filters_to_widgets = True
        return

    loaded = _load_persisted_filters()
    st.session_state.filters = loaded if loaded is not None else _default_filters()
    st.session_state._filters_initialized = True
    st.session_state._apply_filters_to_widgets = True


def _sync_widget_keys_from_filters(filters: dict[str, Any]) -> None:
    for key in ("region_keys", "borough_keys", "neighborhoods", "space_types"):
        st.session_state[key] = list(filters.get(key, []))
    st.session_state["post_kind"] = filters["post_kind"]
    for key in ARRANGEMENTS:
        st.session_state[f"arrangement_{key}"] = key in filters["arrangements"]
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


def _collect_filters_from_widgets() -> dict[str, Any]:
    borough_keys = list(st.session_state.get("borough_keys", []))
    neighborhoods = list(st.session_state.get("neighborhoods", []))
    space_types = list(st.session_state.get("space_types", []))
    arrangements = [key for key in ARRANGEMENTS if st.session_state.get(f"arrangement_{key}", False)]
    date_mode: DateFilterMode = st.session_state.get("date_mode", "none")
    filters: dict[str, Any] = {
        "region_keys": list(st.session_state.get("region_keys", [])),
        "borough_keys": borough_keys if st.session_state.get("region_keys") == [NYC_REGION] else [],
        "neighborhoods": neighborhoods,
        "filter_version": 2,
        "space_types": space_types,
        "arrangements": arrangements,
        "post_kind": st.session_state.get("post_kind", "all"),
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


@st.cache_resource
def _photo_executor():
    return concurrent.futures.ThreadPoolExecutor(max_workers=GALLERY_PREFETCH_WORKERS)



def _gallery_session_key(url: str) -> str:
    return f"_gallery_urls_{st.session_state.get('_access_key', '')}_{_card_key_id(url)}"


def _fetch_gallery(row: ListingRow, cookie: str | None) -> tuple[str, ...]:
    try:
        return fetch_listing_photo_urls(row.url, cookie=cookie) or ((row.photo_url,) if row.photo_url else ())
    except Exception:
        return (row.photo_url,) if row.photo_url else ()


def _prefetch_page_galleries(rows: list[ListingRow], cookie: str | None = None) -> None:
    jobs = st.session_state.setdefault("_gallery_jobs", {})
    for row in rows:
        key = _gallery_session_key(row.url)
        if key not in st.session_state and key not in jobs:
            jobs[key] = _photo_executor().submit(_fetch_gallery, row, cookie)


def _collect_gallery_results() -> bool:
    jobs = st.session_state.setdefault("_gallery_jobs", {})
    changed = False
    for key, future in list(jobs.items()):
        if future.done():
            st.session_state[key] = future.result()
            del jobs[key]
            changed = True
    return changed


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


@st.cache_resource
def _listings_store(cookie: str | None) -> ListingsStore:
    return ListingsStore(Path(__file__).parent, cookie)



def _area_keys(row: ListingRow) -> set[str]:
    return {f"{region}::{name}" for region in (row.region_keys or (row.region_key,))
            for name in row.neighborhood_names}


def _area_label(key: str, regions: dict) -> str:
    region, name = key.split("::", 1)
    label = regions[region].label if region in regions else region.replace("-", " ").title()
    return f"{name} · {label}"


def _areas_changed():
    st.session_state["neighborhoods"] = []
    st.session_state.filters["neighborhoods"] = []


def _region_changed():
    regions = st.session_state.get("region_keys", [])
    _areas_changed()
    if regions != [NYC_REGION]:
        st.session_state["borough_keys"] = []
        st.session_state.filters["borough_keys"] = []
    st.session_state.filters["region_keys"] = list(regions)


def _refresh_error_message(source: str, message: str, regions: dict) -> str:
    if source == "first" and message == "No first-access regions available; check membership/session":
        return (
            "First-access listings could not be checked: Listings Project returned no "
            "first-access regions. Your saved sign-in may have expired, or your membership "
            "may not include first access. Check your membership and reconnect your "
            "Listings Project session, then select Refresh listings. "
            "Public listings can still update; any saved first-access results remain visible."
        )
    if ":" in source:
        kind, key = source.split(":", 1)
        label = regions[key].label if key in regions else key.replace("-", " ").title()
        scope = f"{label} ({'first access' if kind == 'first' else 'public listings'})"
        message = message.removeprefix(f"{label}: ")
    else:
        scope = {"first": "First-access region lookup", "public": "Public region lookup"}.get(source, "Listings refresh")
    return (f"{scope} could not be refreshed. Reason: {message}. "
            "Saved results for this source remain visible, but may be out of date. "
            "Select Refresh listings to try again.")


def _show_refresh_status(snapshot: dict):
    if snapshot["refreshing"]:
        total = snapshot["total"]
        text = (f"Checking for updates · {snapshot['completed']} of {total} regional indexes"
                if total else "Discovering regions…")
        st.caption(text)
    elif snapshot["updated_at"]:
        updated = datetime.fromtimestamp(snapshot["updated_at"]).strftime("%b %d, %I:%M %p")
        st.caption(f"Cached results · last updated {updated}")


def _show_refresh_errors(snapshot: dict):
    for source, message in snapshot["errors"].items():
        st.warning(_refresh_error_message(source, message, snapshot["regions"]))


@st.fragment(run_every=1)
def _refresh_monitor(store: ListingsStore):
    store.start()
    current = store.snapshot()
    gallery_changed = _collect_gallery_results()
    if current["revision"] != st.session_state.get("_store_revision") or gallery_changed:
        st.rerun()
    _show_refresh_status(current)


def filter_rows(
    rows: list[ListingRow],
    *,
    region_keys: set[str],
    borough_keys: set[BoroughKey],
    neighborhoods: set[str],
    space_types: set[str],
    arrangements: set[str],
    post_kind: str,
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
        if region_keys and not region_keys.intersection(row.region_keys or (row.region_key,)):
            continue
        if date_mode != "none" and (row.listing_start is None or row.listing_end is None):
            continue
        if first_access_only and not row.is_first_access:
            continue
        if new_only and row.url not in new_urls:
            continue
        if borough_keys and row.borough_key not in borough_keys:
            continue
        if neighborhoods and not (neighborhoods & _area_keys(row)):
            continue
        category = classify_category(row.listing_type)
        if space_types and category.space_type not in space_types:
            continue
        if arrangements and category.arrangement not in arrangements:
            continue
        if post_kind != "all" and category.post_kind != post_kind:
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
    if s in ("mo", "month", "monthly"):
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
    if row.listing_start is None or row.listing_end is None:
        return row.price
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
        if row.region_key != NYC_REGION:
            location = row.neighborhood
        region_labels = [st.session_state.get("_region_labels", {}).get(key, row.region_label)
                         for key in (row.region_keys or (row.region_key,))]
        extra_regions = [label for label in region_labels
                         if location.casefold() != label.casefold()
                         and not location.casefold().endswith(", " + label.casefold())]
        location_esc = html_lib.escape(" · ".join([location] + extra_regions))
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


st.set_page_config(page_title="ListingProject Explorer", layout="wide")
inject_theme()
st.title("ListingProject Explorer")
st.caption("Find your next place across every Listings Project region.")
_init_filters()
auth_cookie = _load_auth_cookie()
context_key = access_key(auth_cookie)
if st.session_state.get("_access_key") != context_key:
    for key in ("_known_urls", "_new_urls", "_seen_baseline", "_pending_added", "_store_cycle", "_notify_refresh", "_listing_order"):
        st.session_state.pop(key, None)
    st.session_state._access_key = context_key

store = _listings_store(auth_cookie)
store.start()
snapshot = store.snapshot()
st.session_state._store_revision = snapshot["revision"]
all_rows = snapshot["rows"]
# Append newly arrived cards without shifting the page the user is reading.
listing_order = st.session_state.setdefault("_listing_order", {})
for row in all_rows:
    if row.url not in listing_order:
        listing_order[row.url] = len(listing_order)
all_rows.sort(key=lambda row: listing_order[row.url])
regions = snapshot["regions"]
st.session_state._region_labels = {key: region.label for key, region in regions.items()}
current_urls = {row.url for row in all_rows}
if "_known_urls" not in st.session_state:
    st.session_state._seen_baseline = _load_seen_urls()
    st.session_state._known_urls = current_urls
    st.session_state._pending_added = set()
    st.session_state._notify_refresh = bool(current_urls)
    st.session_state._store_cycle = snapshot["cycle"]
else:
    st.session_state._pending_added.update(current_urls - st.session_state._known_urls)
    st.session_state._known_urls = current_urls
st.session_state._new_urls = current_urls - st.session_state._seen_baseline
refresh_completed = snapshot["cycle"] != st.session_state._store_cycle

filters = st.session_state.filters
if st.session_state.pop("_apply_filters_to_widgets", False):
    _sync_widget_keys_from_filters(filters)

# Also hydrate list widgets when an already-open session reloads this UI update.
for key in ("borough_keys", "neighborhoods", "space_types"):
    st.session_state.setdefault(key, list(filters[key]))

with st.sidebar, st.container(key="sidebar_filters"):
    st.header("Filters")
    st.toggle("First access only", key="first_access_only", width="stretch")
    st.toggle("New only", key="new_only", width="stretch")
    region_options = sorted(set(regions) | set(st.session_state.get("region_keys", [])),
                            key=lambda key: regions[key].label if key in regions else key)
    selected_regions = checked_select(
        "Region", region_options, key="region_keys", placeholder="All regions",
        format_func=lambda key: regions[key].label if key in regions else key.replace("-", " ").title(),
        on_change=_region_changed,
    )
    selected_borough_keys = set()
    if selected_regions == [NYC_REGION]:
        borough_labels = {key: label for label, key in BOROUGH_LABELS}
        selected_borough_keys = set(checked_select(
            "Borough", list(borough_labels), key="borough_keys",
            placeholder="All boroughs", format_func=borough_labels.get,
            on_change=_areas_changed,
        ))
    region_rows = [row for row in all_rows
                   if (not selected_regions or set(selected_regions).intersection(row.region_keys or (row.region_key,)))
                   and (not selected_borough_keys or row.borough_key in selected_borough_keys)]
    hood_options = sorted(set().union(*(_area_keys(row) for row in region_rows)))
    # Retain saved areas while their region is still loading or unavailable.
    hood_options = sorted(set(hood_options) | {
        key for key in filters["neighborhoods"]
        if (not selected_regions or key.split("::", 1)[0] in selected_regions)
    })
    selected_neighborhoods = set(checked_select(
        "Neighborhood / Area", hood_options, key="neighborhoods",
        placeholder="All neighborhoods / areas",
        format_func=lambda key: _area_label(key, regions),
    ))
    if st.session_state.pop("_category_migration_notice", False):
        st.info("Your saved categories now use independent space and arrangement filters. "
                "Every selected combination is included—for example, Apartment + House with "
                "Rent + Sublet also includes houses for sublet.")
    selected_post_kind = st.selectbox("Listings", list(POST_KINDS), key="post_kind",
                                       format_func=POST_KINDS.get)
    selected_space_types = set(checked_select(
        "Space type", list(SPACE_TYPES), key="space_types", format_func=SPACE_TYPES.get,
        placeholder="All space types",
    ))
    eligible = [classify_category(row.listing_type) for row in region_rows
                if not selected_neighborhoods or selected_neighborhoods & _area_keys(row)]
    available_arrangements = {c.arrangement for c in eligible
                              if (selected_post_kind == "all" or c.post_kind == selected_post_kind)
                              and (not selected_space_types or c.space_type in selected_space_types)}
    st.markdown("Arrangement")
    st.caption("No boxes checked means all arrangements.")
    for key, label in ARRANGEMENTS.items():
        widget_key = f"arrangement_{key}"
        st.session_state.setdefault(widget_key, key in filters["arrangements"])
        if key in available_arrangements or st.session_state[widget_key]:
            st.checkbox(label, key=widget_key)
    if not available_arrangements and not any(st.session_state.get(f"arrangement_{k}") for k in ARRANGEMENTS):
        st.caption("No arrangements available for these filters yet.")
    selected_arrangements = {k for k in ARRANGEMENTS if st.session_state.get(f"arrangement_{k}", False)}

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
        flexible_months = checked_select(
            "Go anytime",
            options=[key for _, key in month_options],
            format_func=lambda key: month_labels.get(key, key),
            key="flexible_months",
        )
        if not flexible_months:
            st.caption("Select one or more months to filter by availability.")

    st.session_state.filters = _collect_filters_from_widgets()
    _save_persisted_filters(st.session_state.filters)

    if st.button("Clear filters", use_container_width=True):
        st.session_state.filters = _default_filters()
        _clear_persisted_filters()
        _save_persisted_filters(st.session_state.filters)
        st.session_state._apply_filters_to_widgets = True
        st.rerun()

with st.sidebar, st.container(key="sidebar_footer"):
    _refresh_monitor(store)
    st.button("Refresh listings", key="refresh_listings", use_container_width=True,
              type="primary", on_click=store.start, kwargs={"force": True})

_show_refresh_errors(snapshot)

filtered = filter_rows(
    all_rows,
    region_keys=set(selected_regions),
    borough_keys=selected_borough_keys,
    neighborhoods=selected_neighborhoods,
    space_types=selected_space_types,
    arrangements=selected_arrangements,
    post_kind=selected_post_kind,
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
        "region_keys": sorted(selected_regions),
        "borough_keys": sorted(selected_borough_keys),
        "neighborhoods": sorted(selected_neighborhoods),
        "space_types": sorted(selected_space_types),
        "arrangements": sorted(selected_arrangements),
        "post_kind": selected_post_kind,
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

if refresh_completed:
    st.session_state._store_cycle = snapshot["cycle"]
    additions = st.session_state._pending_added & {row.url for row in filtered}
    if additions and st.session_state._notify_refresh:
        st.toast(f"{len(additions)} new listing{'s' if len(additions) != 1 else ''} match your search.", icon="✨")
    st.session_state._pending_added = set()
    st.session_state._notify_refresh = True

if not filtered:
    if snapshot["refreshing"]:
        st.info("Loading regions in the background. Results will appear here as they arrive.")
        st.html('<div class="lp-loading-skeleton" aria-label="Loading listings"></div>')
    else:
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
                "region": "; ".join(regions[key].label if key in regions else key for key in (r.region_keys or (r.region_key,))),
                "available_from": r.listing_start.date() if r.listing_start else None,
                "available_to": r.listing_end.date() if r.listing_end else None,
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
        file_name="listingproject_explorer.csv",
        mime="text/csv",
    )

    # Only visible cards count as seen; background arrivals remain new.
    visible_urls = {row.url for row in page_rows}
    if not visible_urls.issubset(st.session_state.get("_saved_visible_urls", set())):
        _save_seen_urls(_load_seen_urls() | visible_urls)
        st.session_state.setdefault("_saved_visible_urls", set()).update(visible_urls)
