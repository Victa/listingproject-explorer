"""Thread-safe regional snapshots. Background workers never call Streamlit."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, fields
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import pickle
import threading
import time

import httpx

from listing_scraper import (
    FIRST_ACCESS_URL, NYC_REGION, REGIONS_URL, ListingRow, Region,
    discover_regions, fetch_page, fetch_region_listings, merge_listings,
)

SCHEMA_VERSION = 2
CACHE_TTL_SECONDS = 6 * 60 * 60


def access_key(cookie: str | None) -> str:
    return hashlib.sha256((cookie or "").encode()).hexdigest()[:16]


def row_to_json(row: ListingRow) -> dict:
    data = asdict(row)
    for field in ("listing_start", "listing_end"):
        data[field] = data[field].isoformat() if data[field] else None
    return data


def row_from_json(data: dict) -> ListingRow:
    data = dict(data)
    for field in ("listing_start", "listing_end"):
        data[field] = datetime.fromisoformat(data[field]) if data.get(field) else None
    for field in ("neighborhood_names", "region_keys"):
        data[field] = tuple(data.get(field, ()))
    return ListingRow(**data)


class ListingsStore:
    def __init__(self, directory: Path, cookie: str | None = None):
        self.directory = directory
        self.cookie = cookie
        self.key = access_key(cookie)
        self.path = directory / f".listings_cache_v2_{self.key}.json"
        self.lock = threading.RLock()
        self.sources = {}
        self.regions = {}
        self.errors = {}
        self.refreshing = False
        self.completed = 0
        self.total = 0
        self.revision = 0
        self.cycle = 0
        self.last_attempt = 0.0
        self.last_complete = 0.0
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="listings-refresh")
        self.future = None
        self._load()

    def _load(self):
        try:
            payload = json.loads(self.path.read_text())
            if payload["schema"] != SCHEMA_VERSION or payload["access_key"] != self.key:
                return
            sources = {
                key: {"fetched_at": float(source["fetched_at"]),
                      "rows": [row_from_json(row) for row in source["rows"]]}
                for key, source in payload["sources"].items()
            }
            regions = {key: Region(**value) for key, value in payload["regions"].items()}
            self.sources, self.regions = sources, regions
            self.last_complete = float(payload.get("last_complete", 0))
            self.errors = payload.get("errors", {})
            return
        except (OSError, ValueError, KeyError, TypeError):
            pass
        # One-time import of this app's existing, local NYC pickle cache. Stale
        # data remains useful while the first all-region refresh is in flight.
        legacy = self.directory / ".listings_cache.pkl"
        try:
            with legacy.open("rb") as handle:
                payload = pickle.load(handle)
            if payload.get("cookie_key") != self.key:
                return
            timestamp = payload["fetched_at"].replace(tzinfo=timezone.utc).timestamp()
            rows = [ListingRow(**{field.name: getattr(row, field.name, field.default)
                                  for field in fields(ListingRow)}) for row in payload["rows"]]
            self.regions[NYC_REGION] = Region(NYC_REGION, "New York City", f"{REGIONS_URL}/{NYC_REGION}")
            for first in (False, True):
                selected = [row for row in rows if row.is_first_access == first]
                if selected:
                    self.sources[f"{'first' if first else 'public'}:{NYC_REGION}"] = {
                        "fetched_at": timestamp, "rows": selected}
        except (OSError, ValueError, KeyError, TypeError, AttributeError, EOFError, pickle.UnpicklingError):
            pass

    def _save_locked(self):
        payload = {
            "schema": SCHEMA_VERSION, "access_key": self.key,
            "regions": {key: asdict(region) for key, region in self.regions.items()},
            "sources": {key: {"fetched_at": source["fetched_at"],
                                "rows": [row_to_json(row) for row in source["rows"]]}
                        for key, source in self.sources.items()},
            "last_complete": self.last_complete, "errors": self.errors,
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False))
        temporary.replace(self.path)

    def snapshot(self) -> dict:
        with self.lock:
            rows = merge_listings([row for key in sorted(self.sources)
                                   for row in self.sources[key]["rows"]])
            return {
                "rows": rows, "regions": dict(self.regions), "errors": dict(self.errors),
                "refreshing": self.refreshing, "completed": self.completed,
                "total": self.total, "revision": self.revision, "cycle": self.cycle,
                "updated_at": min((s["fetched_at"] for s in self.sources.values()), default=None),
                "last_complete": self.last_complete,
            }

    def start(self, *, force: bool = False) -> bool:
        with self.lock:
            now = time.time()
            if self.refreshing:
                return False
            if not force:
                if now - self.last_attempt < 300:
                    return False
                if self.last_complete and now - self.last_complete < CACHE_TTL_SECONDS and not self.errors:
                    return False
            self.refreshing = True
            self.last_attempt = now
            self.completed = self.total = 0
            self.errors = {}
            self.revision += 1
            self.future = self.executor.submit(self._refresh)
            return True

    def _client(self):
        return httpx.Client(timeout=30, headers={
            "User-Agent": "ListingProjectAdvancedSearch/2.0",
            **({"Cookie": self.cookie} if self.cookie else {}),
        })

    def _fetch(self, region: Region, first_access: bool):
        with self._client() as client:
            return fetch_region_listings(region, client=client, first_access=first_access)

    def _refresh(self):
        try:
            tasks = []
            discovered = {}
            for first_access in ([False, True] if self.cookie else [False]):
                kind = "first" if first_access else "public"
                try:
                    with self._client() as client:
                        html = fetch_page(client, FIRST_ACCESS_URL if first_access else REGIONS_URL)
                    regions = discover_regions(html, first_access=first_access)
                    if not regions:
                        raise ValueError("No first-access regions available; check membership/session"
                                         if first_access else "Region directory could not be read")
                    if first_access:
                        regions = [Region(region.key, self.regions[region.key].label if region.key in self.regions
                                          else region.label, region.url) for region in regions]
                    discovered[kind] = {region.key for region in regions}
                    with self.lock:
                        self.regions.update({region.key: region for region in regions})
                        self.revision += 1
                    tasks.extend((f"{kind}:{region.key}", region, first_access) for region in regions)
                except Exception as exc:
                    with self.lock:
                        self.errors[kind] = str(exc)
            with self.lock:
                self.total = len(tasks)
            with ThreadPoolExecutor(max_workers=3, thread_name_prefix="listings-region") as pool:
                pending = {pool.submit(self._fetch, region, first): (key, region)
                           for key, region, first in tasks}
                for future in as_completed(pending):
                    key, region = pending[future]
                    try:
                        rows = future.result()
                        with self.lock:
                            self.sources[key] = {"fetched_at": time.time(), "rows": rows}
                            self._save_locked()
                    except Exception as exc:
                        with self.lock:
                            self.errors[key] = f"{region.label}: {exc}"
                    finally:
                        with self.lock:
                            self.completed += 1
                            self.revision += 1
            with self.lock:
                # Remove regions only after successful directory discovery for that
                # access level. A failed directory never wipes known results.
                for key in list(self.sources):
                    kind, region_key = key.split(":", 1)
                    if kind in discovered and region_key not in discovered[kind]:
                        del self.sources[key]
                active_keys = set().union(*discovered.values()) if discovered else set()
                active_keys.update(key.split(":", 1)[1] for key in self.sources)
                self.regions = {key: region for key, region in self.regions.items() if key in active_keys}
                if not self.errors:
                    self.last_complete = time.time()
                self._save_locked()
        except Exception as exc:
            with self.lock:
                self.errors["refresh"] = str(exc)
        finally:
            with self.lock:
                self.refreshing = False
                self.cycle += 1
                self.revision += 1
