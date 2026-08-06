"""Fetch and parse Listings Project NYC index pages (server-side; no browser CORS)."""

from __future__ import annotations

import difflib
import html as html_lib
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Literal
from urllib.parse import urlparse, urlunparse

import httpx

BASE_URL = "https://www.listingsproject.com/real-estate/new-york-city"
FIRST_ACCESS_BASE = "https://www.listingsproject.com/real-estate/first-access/new-york-city"
# All NYC real-estate categories. First access offers a shifting subset of these;
# categories that aren't currently offered 302-redirect and are skipped per crawl
# (see the ``is_redirect`` guard in ``fetch_all_listings``).
FIRST_ACCESS_CATEGORIES: tuple[str, ...] = (
    "rentals",
    "studios",
    "sublets",
    "seeking_living",
    "commercial",
    "production",
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
LD_JSON_RE = re.compile(
    r"""<script\s+type=["']application/ld\+json["']>(.*?)</script>""",
    re.IGNORECASE | re.DOTALL,
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

# Canonical neighborhood name -> known alias spellings (per borough).
# Aliases are matched after _normalize_hood (lowercase, no punctuation).
NEIGHBORHOOD_ALIASES: dict[BoroughKey, dict[str, tuple[str, ...]]] = {
    "brooklyn": {
        "Bedford-Stuyvesant": (
            "bedford stuyvesant",
            "bedford-stuyvesant",
            "bed stuy",
            "bed-stuy",
            "bedstuy",
            "stuyvesant heights",
        ),
        "Prospect Lefferts Gardens": (
            "prospect lefferts gardens",
            "prospect lefferts",
            "plg",
        ),
        "Prospect Heights": ("prospect heights",),
        "Clinton Hill": ("clinton hill",),
        "Fort Greene": ("fort greene",),
        "Ocean Hill": ("ocean hill",),
        "Park Slope": ("park slope",),
        "South Slope": ("south slope",),
        "Greenwood": ("greenwood", "greenwood heights"),
        "Sunset Park": ("sunset park",),
        "Bay Ridge": ("bay ridge",),
        "Borough Park": ("borough park", "boro park"),
        "Dyker Heights": ("dyker heights",),
        "Bensonhurst": ("bensonhurst",),
        "Gravesend": ("gravesend",),
        "Coney Island": ("coney island",),
        "Brighton Beach": ("brighton beach",),
        "Sheepshead Bay": ("sheepshead bay",),
        "Flatbush": ("flatbush",),
        "East Flatbush": ("east flatbush",),
        "Crown Heights": ("crown heights",),
        "Weeksville": ("weeksville",),
        "Bushwick": ("bushwick",),
        "East Williamsburg": ("east williamsburg",),
        "Williamsburg": ("williamsburg", "wburg", "wburg."),
        "Greenpoint": ("greenpoint",),
        "Dumbo": ("dumbo", "d.u.m.b.o.", "down under the manhattan bridge overpass"),
        "Brooklyn Heights": ("brooklyn heights",),
        "Cobble Hill": ("cobble hill",),
        "Carroll Gardens": ("carroll gardens",),
        "Boerum Hill": ("boerum hill",),
        "Gowanus": ("gowanus",),
        "Red Hook": ("red hook",),
        "Downtown Brooklyn": ("downtown brooklyn", "downtown bk"),
        "Navy Yard": ("navy yard", "brooklyn navy yard"),
        "Vinegar Hill": ("vinegar hill",),
        "Prospect Park South": ("prospect park south",),
        "Windsor Terrace": ("windsor terrace",),
        "Kensington": ("kensington",),
        "Ditmas Park": ("ditmas park",),
        "Midwood": ("midwood",),
        "Marine Park": ("marine park",),
        "Canarsie": ("canarsie",),
        "East New York": ("east new york", "eny"),
        "Brownsville": ("brownsville",),
        "Cypress Hills": ("cypress hills",),
        "Bergen Beach": ("bergen beach",),
        "Mill Basin": ("mill basin",),
        "Gerritsen Beach": ("gerritsen beach",),
        "Columbia Street Waterfront": (
            "columbia street waterfront",
            "columbia waterfront",
        ),
    },
    "manhattan": {
        "Upper West Side": ("upper west side", "uws"),
        "Upper East Side": ("upper east side", "ues"),
        "Midtown": ("midtown", "midtown manhattan"),
        "Midtown East": ("midtown east",),
        "Midtown West": ("midtown west",),
        "Hell's Kitchen": ("hells kitchen", "hell's kitchen", "clinton"),
        "Chelsea": ("chelsea",),
        "Flatiron": ("flatiron", "flatiron district"),
        "Gramercy": ("gramercy", "gramercy park"),
        "Murray Hill": ("murray hill",),
        "Kips Bay": ("kips bay",),
        "NoMad": ("nomad", "no mad"),
        "Union Square": ("union square",),
        "Greenwich Village": ("greenwich village", "the village"),
        "West Village": ("west village",),
        "East Village": ("east village",),
        "Lower East Side": ("lower east side", "les"),
        "SoHo": ("soho", "so ho"),
        "NoHo": ("noho", "no ho"),
        "NoLita": ("nolita", "no lita"),
        "Little Italy": ("little italy",),
        "Chinatown": ("chinatown",),
        "Tribeca": ("tribeca", "tri beca"),
        "Financial District": ("financial district", "fidi", "fi di"),
        "Battery Park City": ("battery park city", "bpc"),
        "Civic Center": ("civic center",),
        "Two Bridges": ("two bridges",),
        "Harlem": ("harlem",),
        "East Harlem": ("east harlem", "spanish harlem", "el barrio"),
        "Central Harlem": ("central harlem",),
        "West Harlem": ("west harlem",),
        "Hamilton Heights": ("hamilton heights",),
        "Washington Heights": ("washington heights", "wash heights"),
        "Inwood": ("inwood",),
        "Morningside Heights": ("morningside heights",),
        "Manhattan Valley": ("manhattan valley",),
        "Lincoln Square": ("lincoln square",),
        "Theater District": ("theater district", "theatre district"),
        "Times Square": ("times square",),
        "Hudson Yards": ("hudson yards",),
        "Meatpacking District": ("meatpacking district", "meatpacking", "meat packing"),
        "Stuyvesant Town": ("stuyvesant town", "stuy town"),
        "Peter Cooper Village": ("peter cooper village",),
        "Roosevelt Island": ("roosevelt island",),
        "Yorkville": ("yorkville",),
        "Lenox Hill": ("lenox hill",),
        "Carnegie Hill": ("carnegie hill",),
        "Turtle Bay": ("turtle bay",),
        "Sutton Place": ("sutton place",),
        "Beekman": ("beekman", "beekman place"),
    },
    "queens": {
        "Astoria": ("astoria",),
        "Long Island City": ("long island city", "lic"),
        "Sunnyside": ("sunnyside",),
        "Woodside": ("woodside",),
        "Jackson Heights": ("jackson heights",),
        "Elmhurst": ("elmhurst",),
        "Corona": ("corona",),
        "Flushing": ("flushing",),
        "Forest Hills": ("forest hills",),
        "Rego Park": ("rego park",),
        "Kew Gardens": ("kew gardens",),
        "Kew Gardens Hills": ("kew gardens hills",),
        "Briarwood": ("briarwood",),
        "Jamaica": ("jamaica",),
        "Jamaica Estates": ("jamaica estates",),
        "Hollis": ("hollis",),
        "Queens Village": ("queens village",),
        "Bayside": ("bayside",),
        "Whitestone": ("whitestone",),
        "College Point": ("college point",),
        "Fresh Meadows": ("fresh meadows",),
        "Oakland Gardens": ("oakland gardens",),
        "Douglaston": ("douglaston",),
        "Little Neck": ("little neck",),
        "Ridgewood": ("ridgewood",),
        "Glendale": ("glendale",),
        "Middle Village": ("middle village",),
        "Maspeth": ("maspeth",),
        "Woodhaven": ("woodhaven",),
        "Ozone Park": ("ozone park",),
        "Howard Beach": ("howard beach",),
        "South Ozone Park": ("south ozone park",),
        "Richmond Hill": ("richmond hill",),
        "South Richmond Hill": ("south richmond hill",),
        "Rockaway Beach": ("rockaway beach", "the rockaways", "rockaways"),
        "Far Rockaway": ("far rockaway",),
        "Breezy Point": ("breezy point",),
        "Ditmars": ("ditmars", "ditmars steinway"),
        "Steinway": ("steinway",),
        "Hunters Point": ("hunters point", "hunter's point"),
        "Dutch Kills": ("dutch kills",),
        "Ravenswood": ("ravenswood",),
    },
    "bronx": {
        "Riverdale": ("riverdale",),
        "Kingsbridge": ("kingsbridge",),
        "Marble Hill": ("marble hill",),
        "Fordham": ("fordham",),
        "Belmont": ("belmont",),
        "University Heights": ("university heights",),
        "Morris Heights": ("morris heights",),
        "Highbridge": ("highbridge", "high bridge"),
        "Concourse": ("concourse", "the concourse"),
        "Mott Haven": ("mott haven",),
        "Port Morris": ("port morris",),
        "Melrose": ("melrose",),
        "Morrisania": ("morrisania",),
        "Tremont": ("tremont",),
        "West Farms": ("west farms",),
        "Crotona Park": ("crotona park", "crotona"),
        "Bronx Park": ("bronx park",),
        "Norwood": ("norwood",),
        "Bedford Park": ("bedford park",),
        "Williamsbridge": ("williamsbridge",),
        "Wakefield": ("wakefield",),
        "Edenwald": ("edenwald",),
        "Eastchester": ("eastchester",),
        "Baychester": ("baychester",),
        "Co-op City": ("co-op city", "coop city"),
        "Pelham Bay": ("pelham bay",),
        "Pelham Parkway": ("pelham parkway",),
        "Morris Park": ("morris park",),
        "Van Nest": ("van nest",),
        "Westchester Square": ("westchester square",),
        "Castle Hill": ("castle hill",),
        "Parkchester": ("parkchester",),
        "Soundview": ("soundview",),
        "Hunts Point": ("hunts point", "hunt's point"),
        "Longwood": ("longwood",),
        "Claremont": ("claremont",),
        "Throgs Neck": ("throgs neck", "throggs neck"),
        "City Island": ("city island",),
        "Country Club": ("country club",),
        "Spuyten Duyvil": ("spuyten duyvil",),
        "Fieldston": ("fieldston",),
    },
    "staten_island": {
        "St. George": ("st george", "st. george", "saint george"),
        "Tompkinsville": ("tompkinsville",),
        "Stapleton": ("stapleton",),
        "Clifton": ("clifton",),
        "Concord": ("concord",),
        "Grymes Hill": ("grymes hill",),
        "Silver Lake": ("silver lake",),
        "West Brighton": ("west brighton", "westbrighton"),
        "New Brighton": ("new brighton",),
        "Snug Harbor": ("snug harbor",),
        "Port Richmond": ("port richmond",),
        "Mariners Harbor": ("mariners harbor", "mariner's harbor"),
        "Graniteville": ("graniteville",),
        "Westerleigh": ("westerleigh",),
        "Castleton Corners": ("castleton corners",),
        "Todt Hill": ("todt hill",),
        "Dongan Hills": ("dongan hills",),
        "New Dorp": ("new dorp",),
        "Oakwood": ("oakwood",),
        "Great Kills": ("great kills",),
        "Eltingville": ("eltingville",),
        "Annadale": ("annadale",),
        "Huguenot": ("huguenot",),
        "Prince's Bay": ("princes bay", "prince's bay"),
        "Tottenville": ("tottenville",),
        "Charleston": ("charleston",),
        "Rossville": ("rossville",),
        "Arden Heights": ("arden heights",),
        "Willowbrook": ("willowbrook",),
        "Bulls Head": ("bulls head", "bull's head"),
        "Travis": ("travis",),
        "Midland Beach": ("midland beach",),
        "South Beach": ("south beach",),
        "Fort Wadsworth": ("fort wadsworth",),
    },
}

_BOROUGH_STRIP_RE = re.compile(
    r""",?\s*(?:Brooklyn|Queens|Bronx|Staten Island|Manhattan|New York)\s*$""",
    re.IGNORECASE,
)
_HOOD_SPLIT_RE = re.compile(r"\s*[,/&]|\s+and\s+", re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]+")
_BOROUGH_TOKEN_NORMS = frozenset(
    {
        "brooklyn",
        "queens",
        "bronx",
        "staten island",
        "manhattan",
        "new york",
        "nyc",
    }
)
_FUZZY_CUTOFF = 0.82


def _normalize_hood(s: str) -> str:
    """Lowercase, unescape, drop punctuation/hyphens, collapse whitespace."""
    t = html_lib.unescape(s).lower().strip()
    t = _NON_ALNUM_RE.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def _build_alias_lookups() -> tuple[
    dict[BoroughKey, dict[str, str]],
    dict[BoroughKey, list[str]],
]:
    """Build normalized alias -> canonical lookup and canonical name lists per borough."""
    alias_lookup: dict[BoroughKey, dict[str, str]] = {}
    canonical_by_borough: dict[BoroughKey, list[str]] = {}
    for borough_key, aliases in NEIGHBORHOOD_ALIASES.items():
        lookup: dict[str, str] = {}
        canonicals: list[str] = []
        for canonical, alias_list in aliases.items():
            canonicals.append(canonical)
            lookup[_normalize_hood(canonical)] = canonical
            for alias in alias_list:
                lookup[_normalize_hood(alias)] = canonical
        alias_lookup[borough_key] = lookup
        canonical_by_borough[borough_key] = canonicals
    return alias_lookup, canonical_by_borough


_ALIAS_LOOKUP, _CANONICAL_BY_BOROUGH = _build_alias_lookups()


def _canonicalize_neighborhoods(
    first_segment: str, borough_key: BoroughKey
) -> tuple[str, ...]:
    """
    Map a free-text location segment to one or more canonical neighborhood names.

    Splits on commas/slashes/ampersands, matches each token via alias map then
    fuzzy fallback; unmatched tokens are kept title-cased.
    """
    stripped = _BOROUGH_STRIP_RE.sub("", first_segment).strip()
    if not stripped:
        return ()

    tokens = [t.strip() for t in _HOOD_SPLIT_RE.split(stripped) if t.strip()]
    if not tokens:
        cleaned = re.sub(r"\s+", " ", stripped).strip()
        return (cleaned,) if cleaned else ()

    lookup = _ALIAS_LOOKUP.get(borough_key, {})
    canonicals = _CANONICAL_BY_BOROUGH.get(borough_key, [])
    # Normalized canonical -> display name for fuzzy result mapping
    norm_to_canonical = {_normalize_hood(c): c for c in canonicals}
    fuzzy_choices = list(norm_to_canonical.keys())

    seen: set[str] = set()
    result: list[str] = []
    for token in tokens:
        # Drop street-intersection suffixes like "Franklyn Av x Lafayette Av"
        token = re.sub(r"\s*[-–]\s*.*\bx\b.*$", "", token, flags=re.I).strip()
        if not token:
            continue
        norm = _normalize_hood(token)
        if not norm or norm in _BOROUGH_TOKEN_NORMS:
            continue

        matched: str | None = lookup.get(norm)
        if matched is None and fuzzy_choices:
            close = difflib.get_close_matches(
                norm, fuzzy_choices, n=1, cutoff=_FUZZY_CUTOFF
            )
            if close:
                matched = norm_to_canonical[close[0]]

        if matched is None:
            # Title-case the cleaned token for display
            matched = " ".join(w.capitalize() for w in norm.split())

        if matched not in seen:
            seen.add(matched)
            result.append(matched)

    return tuple(result)


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


def _normalize_detail_photo_url(raw: str) -> str | None:
    """Absolute HTTPS photo URL without CDN sizing query params."""
    url = html_lib.unescape(raw.strip())
    if not url:
        return None
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith("https://"):
        return None
    parsed = urlparse(url)
    # Drop height=/width=/aspect_ratio= so the UI can request a display size.
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def _dedupe_photo_urls(candidates: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in candidates:
        url = _normalize_detail_photo_url(raw)
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(url)
    return tuple(out)


def _photo_urls_from_listing_images(html: str) -> tuple[str, ...]:
    candidates: list[str] = []
    for m in re.finditer(r"<img[^>]+>", html, flags=re.I):
        tag = m.group(0)
        if "listing_image" not in tag.lower():
            continue
        sm = re.search(r'src\s*=\s*"([^"]+)"', tag, flags=re.I)
        if sm:
            candidates.append(sm.group(1))
    return _dedupe_photo_urls(candidates)


def _photo_urls_from_ld_json(html: str) -> tuple[str, ...]:
    candidates: list[str] = []
    for m in LD_JSON_RE.finditer(html):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        images = data.get("image")
        if isinstance(images, str):
            candidates.append(images)
        elif isinstance(images, list):
            candidates.extend(u for u in images if isinstance(u, str))
    return _dedupe_photo_urls(candidates)


def parse_listing_photo_urls(html: str) -> tuple[str, ...]:
    """Photo URLs from a listing detail page (``listing_image``, else JSON-LD)."""
    urls = _photo_urls_from_listing_images(html)
    if urls:
        return urls
    return _photo_urls_from_ld_json(html)


def fetch_listing_photo_urls(
    url: str,
    *,
    cookie: str | None = None,
) -> tuple[str, ...]:
    """GET a listing detail page and return gallery photo URLs (may be empty)."""
    headers: dict[str, str] = {"User-Agent": "ListingProjectLocalTool/1.0"}
    if cookie:
        headers["Cookie"] = cookie
    with httpx.Client(
        timeout=30.0,
        headers=headers,
        follow_redirects=True,
    ) as client:
        r = client.get(url)
        r.raise_for_status()
        return parse_listing_photo_urls(r.text)


@dataclass(frozen=True)
class ListingRow:
    title: str
    # Area + borough from the card line, e.g. "Greenpoint, Brooklyn"
    neighborhood: str
    neighborhood_name: str
    # Canonical neighborhood name(s) derived from neighborhood_name
    neighborhood_names: tuple[str, ...]
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


def _parse_location_line(
    raw_line: str,
) -> tuple[str, tuple[str, ...], str, str, BoroughKey]:
    """
    Parse a card location line into neighborhood_name, neighborhood_names,
    borough_label, listing_type, borough_key.

    Example: ``Park Slope, Brooklyn | Apartments for Sublet`` ->
    (``Park Slope``, (``Park Slope``,), ``Brooklyn``, ``Apartments for Sublet``, ``brooklyn``).
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

    neighborhood_names = _canonicalize_neighborhoods(first_segment, borough_key)
    if not neighborhood_names and neighborhood_name:
        neighborhood_names = (neighborhood_name,)

    return neighborhood_name, neighborhood_names, borough_label, listing_type, borough_key


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
        neighborhood_names=tuple(row["neighborhood_names"]),
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
        (
            neighborhood_name,
            neighborhood_names,
            borough_label,
            listing_type,
            borough_key,
        ) = _parse_location_line(raw_location_line)
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
                "neighborhood_names": neighborhood_names,
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
    """Crawl NYC indexes (first-access when ``cookie`` is set, then public); dedupe by URL."""
    own_client = client is None
    if own_client:
        headers: dict[str, str] = {"User-Agent": "ListingProjectLocalTool/1.0"}
        if cookie:
            headers["Cookie"] = cookie
        client = httpx.Client(timeout=30.0, headers=headers)

    # First-access before public so FA listings win URL dedupe and keep
    # is_first_access=True when they also appear on the public index.
    indexes: list[tuple[str, bool]] = []
    if cookie:
        for category in FIRST_ACCESS_CATEGORIES:
            indexes.append((f"{FIRST_ACCESS_BASE}/{category}", True))
    indexes.append((BASE_URL, False))

    seen_urls: set[str] = set()
    results: list[ListingRow] = []

    try:
        # Discover page counts for every index first so progress is one continuous bar.
        discovered: list[tuple[str, bool, str, int]] = []
        for i, (base_url, is_first_access) in enumerate(indexes):
            if i > 0 and request_delay_s > 0:
                time.sleep(request_delay_s)
            r = client.get(base_url, params={"page": 1})
            # A first-access category that isn't offered redirects (302) back to the
            # base first-access index; skip it instead of aborting the whole crawl.
            if is_first_access and r.is_redirect:
                continue
            r.raise_for_status()
            html = r.text
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
