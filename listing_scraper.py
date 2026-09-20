"""Fetch and parse Listings Project regional index pages (server-side; no browser CORS)."""

from __future__ import annotations

import difflib
import html as html_lib
import json
import re
import time
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from datetime import date, datetime
from typing import Callable, Literal
from urllib.parse import urlparse, urlunparse

import httpx

SITE_URL = "https://www.listingsproject.com"
REGIONS_URL = f"{SITE_URL}/real-estate"
FIRST_ACCESS_URL = f"{REGIONS_URL}/first-access"
NYC_REGION = "new-york-city"


@dataclass(frozen=True)
class Region:
    key: str
    label: str
    url: str


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.href = None
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.parts = []

    def handle_data(self, data):
        if self.href is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.href is not None:
            self.links.append((self.href, " ".join(" ".join(self.parts).split())))
            self.href = None


def discover_regions(html: str, *, first_access: bool = False) -> list[Region]:
    """Use source links and labels, including featured tiles and text-only regions."""
    links = _Links()
    links.feed(html)
    prefix = "/real-estate/first-access/" if first_access else "/real-estate/"
    regions = {}
    for href, label in links.links:
        parsed = urlparse(href)
        if parsed.netloc and parsed.netloc != "www.listingsproject.com":
            continue
        path = parsed.path.rstrip("/")
        if not path.startswith(prefix):
            continue
        key = path[len(prefix):]
        if first_access and "/" in key:
            parts = key.split("/")
            if len(parts) != 2:
                continue
            key = parts[0]
            label = key.replace("-", " ").title()
            path = prefix + key
        if not key or "/" in key or key == "first-access" or not label:
            continue
        regions[key] = Region(key, label, f"{SITE_URL}{path}")
    return sorted(regions.values(), key=lambda region: region.label.casefold())


def discover_category_urls(html: str, region: Region) -> list[str]:
    links = _Links()
    links.feed(html)
    prefix = urlparse(region.url).path.rstrip("/") + "/"
    return list(dict.fromkeys(
        f"{SITE_URL}{urlparse(href).path}" for href, _ in links.links
        if (not urlparse(href).netloc or urlparse(href).netloc == "www.listingsproject.com")
        and urlparse(href).path.startswith(prefix)
        and "/" not in urlparse(href).path[len(prefix):].strip("/")
    ))

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
    r"""((?:(?:US|CA|AU|NZ)?\$|€|£|¥)\s*[0-9][0-9,.\s]*(?:/[A-Za-z]+)?)\s*</span>""",
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
        "East Williamsburg": ("east williamsburg", "east williamburg"),
        "Williamsburg": (
            "williamsburg",
            "wburg",
            "wburg.",
            "north williamsburg",
            "south williamsburg",
            "williamburg",
        ),
        "Greenpoint": ("greenpoint",),
        "Dumbo": ("dumbo", "d.u.m.b.o.", "down under the manhattan bridge overpass"),
        "Brooklyn Heights": ("brooklyn heights",),
        "Cobble Hill": ("cobble hill",),
        "Carroll Gardens": ("carroll gardens",),
        "Boerum Hill": ("boerum hill", "boerum"),
        "Gowanus": ("gowanus",),
        "Red Hook": ("red hook", "red hood"),
        "Downtown Brooklyn": ("downtown brooklyn", "downtown bk"),
        "Navy Yard": ("navy yard", "brooklyn navy yard"),
        "Vinegar Hill": ("vinegar hill",),
        "Prospect Park South": ("prospect park south",),
        "Windsor Terrace": ("windsor terrace",),
        "Kensington": ("kensington",),
        "Ditmas Park": ("ditmas park",),
        "Midwood": ("midwood", "south midwood"),
        "Homecrest": ("homecrest",),
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
            "columbia st waterfront",
            "columbia st waterfront district",
            "columbia street waterfront district",
        ),
    },
    "manhattan": {
        "Upper West Side": ("upper west side", "uws"),
        "Upper East Side": ("upper east side", "ues"),
        "Midtown": (
            "midtown",
            "midtown manhattan",
            "herald square",
            "herald sq",
            "koreatown",
        ),
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
        "East Village": ("east village", "alphabet city"),
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
        "Harlem": ("harlem", "south harlem"),
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
        "Hudson Yards": ("hudson yards", "hudson yard"),
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
    r""",?\s*(?:Brooklyn|Queens|Bronx|Staten Island|Manhattan|New York(?:\s+City)?)\s*$""",
    re.IGNORECASE,
)
_HOOD_SPLIT_RE = re.compile(r"\s*[,/;|&]|\s+and\s+", re.IGNORECASE)
_PAREN_RE = re.compile(r"\(([^)]*)\)")
_APOSTROPHE_RE = re.compile(r"['\u2019\u2018]")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]+")
_STREET_TOKEN_RE = re.compile(
    r"^.+\s+(?:ave|avenue|av|st|street|rd|road|blvd|boulevard)$",
    re.IGNORECASE,
)
_FLUFF_SUFFIX_RE = re.compile(
    r"\s+(?:border|area)\s*$",
    re.IGNORECASE,
)
_JUNK_TOKEN_RE = re.compile(
    r"^(?:steps away from|not too far(?: from the city)?|open to more|within a|etc)\b",
    re.IGNORECASE,
)
_STRIP_LEADING_FLUFF_RE = re.compile(r"^(?:prime)\s+", re.IGNORECASE)
_BOROUGH_TOKEN_NORMS = frozenset(
    {
        "brooklyn",
        "queens",
        "bronx",
        "staten island",
        "manhattan",
        "new york",
        "new york city",
        "nyc",
        "bk",
        "ny",
        "manhatten",
        "brookyln",
        "north brooklyn",
        "lower manhattan",
        "upper manhattan",
        "downtown manhattan",
    }
)
_FUZZY_CUTOFF = 0.82


def _normalize_hood(s: str) -> str:
    """Lowercase, unescape, drop apostrophes/punctuation/hyphens, collapse whitespace."""
    t = html_lib.unescape(s).lower().strip()
    t = _APOSTROPHE_RE.sub("", t)
    t = _NON_ALNUM_RE.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def _build_alias_lookups() -> tuple[
    dict[BoroughKey, dict[str, str]],
    dict[BoroughKey, list[str]],
    dict[str, str],
    list[str],
]:
    """Build per-borough and unique global alias lookups plus canonical name lists."""
    alias_lookup: dict[BoroughKey, dict[str, str]] = {}
    canonical_by_borough: dict[BoroughKey, list[str]] = {}
    global_hits: dict[str, set[str]] = {}
    all_canonicals: list[str] = []
    for borough_key, aliases in NEIGHBORHOOD_ALIASES.items():
        lookup: dict[str, str] = {}
        canonicals: list[str] = []
        for canonical, alias_list in aliases.items():
            canonicals.append(canonical)
            all_canonicals.append(canonical)
            for raw in (canonical, *alias_list):
                norm = _normalize_hood(raw)
                if not norm:
                    continue
                lookup[norm] = canonical
                global_hits.setdefault(norm, set()).add(canonical)
        alias_lookup[borough_key] = lookup
        canonical_by_borough[borough_key] = canonicals
    global_lookup = {
        norm: next(iter(cans)) for norm, cans in global_hits.items() if len(cans) == 1
    }
    return alias_lookup, canonical_by_borough, global_lookup, all_canonicals


(
    _ALIAS_LOOKUP,
    _CANONICAL_BY_BOROUGH,
    _GLOBAL_ALIAS_LOOKUP,
    _ALL_CANONICALS,
) = _build_alias_lookups()
_GLOBAL_NORM_TO_CANONICAL = {_normalize_hood(c): c for c in _ALL_CANONICALS}
_GLOBAL_FUZZY_CHOICES = list(_GLOBAL_NORM_TO_CANONICAL.keys())


def _is_junk_token(norm: str) -> bool:
    """True for borough leftovers, streets, and marketing fluff — not real hoods."""
    if not norm or norm in _BOROUGH_TOKEN_NORMS:
        return True
    if _JUNK_TOKEN_RE.search(norm):
        return True
    if _STREET_TOKEN_RE.search(norm):
        return True
    # Multi-borough marketing lists / non-locations
    if " or " in norm or "open to" in norm:
        return True
    return False


def _extract_hood_tokens(first_segment: str) -> list[str]:
    """Split a location segment into neighborhood candidate tokens."""
    stripped = first_segment.strip()
    if not stripped:
        return []

    tokens: list[str] = []
    for part in _HOOD_SPLIT_RE.split(stripped):
        part = part.strip()
        if not part:
            continue
        # Pull parenthetical aliases out as their own tokens
        for inner in _PAREN_RE.findall(part):
            inner = inner.strip()
            if inner:
                tokens.append(inner)
        part = _PAREN_RE.sub(" ", part)
        # Drop street-intersection suffixes like "Franklyn Av x Lafayette Av"
        part = re.sub(r"\s*[-–]\s*.*\bx\b.*$", "", part, flags=re.I).strip()
        part = _FLUFF_SUFFIX_RE.sub("", part).strip()
        part = _BOROUGH_STRIP_RE.sub("", part).strip()
        part = _STRIP_LEADING_FLUFF_RE.sub("", part).strip()
        part = re.sub(r"\s+", " ", part).strip(" -–")
        if part:
            tokens.append(part)
    return tokens


def _match_hood_token(
    token: str,
    borough_key: BoroughKey,
) -> list[str]:
    """
    Resolve one token to zero or more canonical neighborhood names.
    Uses borough-local alias/fuzzy, then unique global alias, then global fuzzy.
    Unmatched tokens are dropped (not title-cased into the filter).
    """
    norm = _normalize_hood(token)
    if _is_junk_token(norm):
        return []

    lookup = _ALIAS_LOOKUP.get(borough_key, {})
    local_canonicals = _CANONICAL_BY_BOROUGH.get(borough_key, [])
    local_norm_to_canonical = {_normalize_hood(c): c for c in local_canonicals}
    local_fuzzy = list(local_norm_to_canonical.keys())

    matched = lookup.get(norm)
    if matched is None and local_fuzzy:
        close = difflib.get_close_matches(norm, local_fuzzy, n=1, cutoff=_FUZZY_CUTOFF)
        if close:
            matched = local_norm_to_canonical[close[0]]

    if matched is None:
        matched = _GLOBAL_ALIAS_LOOKUP.get(norm)

    if matched is None and _GLOBAL_FUZZY_CHOICES:
        close = difflib.get_close_matches(
            norm, _GLOBAL_FUZZY_CHOICES, n=1, cutoff=_FUZZY_CUTOFF
        )
        if close:
            matched = _GLOBAL_NORM_TO_CANONICAL[close[0]]

    if matched is not None:
        return [matched]

    # Compound like "BedStuy-Clinton Hill": try hyphen parts when whole token misses
    if "-" in token or "–" in token:
        parts = re.split(r"[-–]", token)
        hits: list[str] = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            hits.extend(_match_hood_token(part, borough_key))
        if hits:
            return hits

    return []


def _canonicalize_neighborhoods(
    first_segment: str, borough_key: BoroughKey
) -> tuple[str, ...]:
    """
    Map a free-text location segment to one or more canonical neighborhood names.

    Splits on commas/slashes/semicolons/ampersands, matches each token via
    borough alias map then global fallback; unmatched/junk tokens are dropped.
    """
    tokens = _extract_hood_tokens(first_segment)
    if not tokens:
        return ()

    seen: set[str] = set()
    result: list[str] = []
    for token in tokens:
        for matched in _match_hood_token(token, borough_key):
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
    listing_start: datetime | None
    listing_end: datetime | None
    price: str
    url: str
    is_first_access: bool = False
    region_key: str = NYC_REGION
    region_label: str = "New York City"
    region_keys: tuple[str, ...] = ()


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
    raw_line: str, region_key: str = NYC_REGION,
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

    borough_key = _derive_borough_key(first_segment) if region_key == NYC_REGION else "all"
    if borough_key == "all":
        borough_label = ""
    else:
        borough_label = BOROUGH_LABELS[borough_key]

    if ", " in first_segment:
        neighborhood_name = first_segment.rsplit(", ", 1)[0].strip()
    else:
        neighborhood_name = first_segment.strip()

    neighborhood_names = (
        _canonicalize_neighborhoods(first_segment, borough_key)
        if region_key == NYC_REGION else (neighborhood_name,)
    )

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
        availability=(f"{row['start_date_str']} to {row['end_date_str']}"
                      if row["listing_start"] else "Availability not specified"),
        listing_start=row["listing_start"],
        listing_end=row["listing_end"],
        price=row["price"],
        url=row["url"],
        is_first_access=is_first_access,
        region_key=row.get("region_key", NYC_REGION),
        region_label=row.get("region_label", "New York City"),
        region_keys=(row.get("region_key", NYC_REGION),),
    )


def parse_listings_from_html(html: str, borough: BoroughKey = "all", *, region: Region | None = None) -> list[dict]:
    """Parse listing cards from one index page HTML. No dedupe."""
    region = region or Region(NYC_REGION, "New York City", f"{REGIONS_URL}/{NYC_REGION}")
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
        start_s = date_strings[0] if date_strings else ""
        end_s = date_strings[1] if len(date_strings) > 1 else ""
        start_dt = _parse_listing_date(start_s)
        end_dt = _parse_listing_date(end_s)
        title = _normalize_title(title_raw)
        url = _absolute_url(href)
        (
            neighborhood_name,
            neighborhood_names,
            borough_label,
            listing_type,
            borough_key,
        ) = _parse_location_line(raw_location_line, region.key)
        out.append(
            {
                "region_key": region.key,
                "region_label": region.label,
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


def request_page(client: httpx.Client, url: str, page: int = 1) -> httpx.Response:
    """Retry transient failures only; never follow redirects into another region."""
    for attempt in range(3):
        try:
            response = client.get(url, params={"page": page})
            if response.status_code == 429 or response.status_code >= 500:
                response.raise_for_status()
            return response
        except (httpx.TransportError, httpx.HTTPStatusError):
            if attempt == 2:
                raise
            time.sleep(0.5 * (2 ** attempt))
    raise RuntimeError("Request did not complete")


def fetch_page(client: httpx.Client, url: str, page: int = 1) -> str:
    response = request_page(client, url, page)
    response.raise_for_status()
    return response.text


def fetch_region_listings(
    region: Region, *, client: httpx.Client, first_access: bool = False,
    request_delay_s: float = 0.25,
) -> list[ListingRow]:
    """Return a complete region snapshot, or fail without publishing partial pages."""
    first_html = fetch_page(client, region.url)
    count_pattern = r"([\d,]+)\s+Listings?\b"
    count = re.search(count_pattern, first_html, re.I)
    indexes = [(region.url, first_html)]
    # Some first-access regions offer categories rather than an aggregate index.
    if first_access and count is None:
        categories = discover_category_urls(first_html, region)
        if not categories:
            raise ValueError("First-access listings are unavailable; check membership/session")
        indexes = []
        for url in categories:
            time.sleep(request_delay_s)
            response = request_page(client, url)
            if response.is_redirect:
                continue
            response.raise_for_status()
            indexes.append((url, response.text))
        if not indexes:
            raise ValueError("No first-access categories could be loaded")

    rows = []
    for url, html in indexes:
        expected = re.search(count_pattern, html, re.I)
        if expected is None:
            raise ValueError("Unrecognized listing index; previous results retained")
        index_rows = []
        for page in range(1, discover_max_page(html) + 1):
            if page > 1:
                time.sleep(request_delay_s)
                html = fetch_page(client, url, page)
            parsed = parse_listings_from_html(html, region=region)
            if not parsed and int(expected.group(1).replace(",", "")) > 0:
                raise ValueError("Listing cards could not be parsed; previous results retained")
            index_rows.extend(_row_from_parsed(row, is_first_access=first_access) for row in parsed)
        if len({row.url for row in index_rows}) < int(expected.group(1).replace(",", "")):
            raise ValueError("Incomplete regional index; previous results retained")
        rows.extend(index_rows)
    return merge_listings(rows)


def merge_listings(rows: list[ListingRow]) -> list[ListingRow]:
    """Deduplicate URLs across regions and preserve first-access precedence."""
    merged = {}
    for row in rows:
        previous = merged.get(row.url)
        keys = set(row.region_keys or (row.region_key,))
        if previous:
            keys.update(previous.region_keys or (previous.region_key,))
        preferred = previous if previous and previous.is_first_access else row
        merged[row.url] = replace(preferred, region_keys=tuple(sorted(keys)))
    return list(merged.values())


def fetch_all_listings(
    *, request_delay_s: float = 0.25,
    progress: Callable[[int, int], None] | None = None,
    client: httpx.Client | None = None, cookie: str | None = None,
) -> list[ListingRow]:
    """Fetch all discovered regions. The UI uses the resilient background store."""
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=30, headers={
            "User-Agent": "ListingProjectAdvancedSearch/2.0",
            **({"Cookie": cookie} if cookie else {}),
        })
    try:
        sources = [(region, False) for region in discover_regions(fetch_page(client, REGIONS_URL))]
        if not sources:
            raise ValueError("No regions discovered")
        if cookie:
            sources.extend((region, True) for region in discover_regions(
                fetch_page(client, FIRST_ACCESS_URL), first_access=True))
        rows = []
        for index, (region, first_access) in enumerate(sources):
            rows.extend(fetch_region_listings(region, client=client, first_access=first_access,
                                              request_delay_s=request_delay_s))
            if progress:
                progress(index + 1, len(sources))
        return merge_listings(rows)
    finally:
        if own_client:
            client.close()


def search_listings(
    mode: SearchMode, borough: BoroughKey, *, on_or_after: date | None = None,
    overlap_start: date | None = None, overlap_end: date | None = None,
    on_date: date | None = None, request_delay_s: float = 0.25,
    progress: Callable[[int, int], None] | None = None,
    client: httpx.Client | None = None,
) -> list[ListingRow]:
    """Compatibility helper; a borough selection explicitly restricts results to NYC."""
    if mode == "on_or_after" and on_or_after is None:
        raise ValueError("on_or_after is required")
    if mode == "on_date" and on_date is None:
        raise ValueError("on_date is required")
    if mode == "overlap" and (overlap_start is None or overlap_end is None):
        raise ValueError("overlap_start and overlap_end are required")
    results = []
    for row in fetch_all_listings(client=client, progress=progress, request_delay_s=request_delay_s):
        if borough != "all" and (row.region_key != NYC_REGION or row.borough_key != borough):
            continue
        if row.listing_start is None or row.listing_end is None:
            continue
        start, end = row.listing_start.date(), row.listing_end.date()
        if mode == "on_or_after" and start < on_or_after:
            continue
        if mode == "on_date" and not start <= on_date <= end:
            continue
        if mode == "overlap" and (end < overlap_start or start > overlap_end):
            continue
        results.append(row)
    return results
