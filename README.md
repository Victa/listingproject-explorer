# ListingProject Explorer

Personal Streamlit app for searching every available real-estate region on [listingsproject.com](https://www.listingsproject.com).

Discovers regions directly from Listings Project, caches their listings, and filters in memory. Cached results appear immediately while outdated regions refresh in the background.

## Demo

Try the live demo: [listings-projects.streamlit.app](https://listings-projects.streamlit.app/)

Note: the demo runs with public access only. If you have a listingsproject.com membership and want to use your personal token for first-access listings, you'll need to [run the app locally](#setup) and [set up your token](#optional-first-access-listings).

## Features

- **Region filter** — searchable checkbox dropdown, with all regions included by default. Selected options stay in the list with a checkmark and can be unchecked in place. Newly offered regions are discovered automatically.
- **Location filters** — areas are scoped to their regions; NYC searches also offer boroughs and canonicalized neighborhoods (e.g. "Bed Stuy" → "Bedford-Stuyvesant"). Long selected labels wrap without truncation. Region, borough, neighborhood, space-type, and flexible-month menus retain checked options in their searchable lists.
- **Listings, space type, and arrangement filters** — browse All posts (the default), Available spaces, or Wanted posts. Select space types independently of Rent, Buy, Sublet, Share, Swap, or House sitting. No arrangement boxes checked means all arrangements. Apartment + House with Rent + Sublet includes all four combinations.
- **Source-aware categories** — unspecified arrangements remain Unspecified; unknown categories stay visible under All posts and Other / unspecified without guessing from titles. Arrangement options adapt to location, post kind, and space selections; selected options stay removable even with no matches.
- **Background refresh** — keep browsing while regional snapshots update, with subtle new-card highlights and a toast when new results match your filters. Gallery photos load separately.
- **Date availability** — any dates, exact check-in/check-out (with optional ± flexibility), or flexible week/month stays across selected months
- **New listings** — highlights listings you haven't seen yet across refreshes
- **First access** — optional (requires a session cookie; see below)
- **CSV export** of the filtered results
- **macOS launcher** — double-click `ListingProject Explorer.app` to start Streamlit and open the browser

## Requirements

- Python 3.10+
- Internet access (the app fetches from listingsproject.com)

## Setup

```bash
git clone https://github.com/Victa/listingproject-explorer.git
cd listingproject-explorer
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Then open the local URL Streamlit prints (usually `http://localhost:8501`).

Listings remain fresh for 6 hours (in memory and on disk). Older cached results stay visible while the app refreshes in the background. Click **Refresh listings** to refresh immediately without clearing your results. A failed regional request retains that region's previous snapshot; refresh details explain what could not be updated. While a session is open, failed refreshes can retry after five minutes.

Existing NYC caches and saved searches migrate automatically. Legacy property categories migrate once to independent space and arrangement filters, with a notice that additional combinations can be included. Clear filters restores All posts and unrestricted space types and arrangements. Original source categories remain on cards and in CSV exports. Existing searches retain an explicit NYC selection; **Clear filters** starts a search across all regions. Listings without specified dates appear under **Any dates**. Prices retain the source currency; non-dollar prices are displayed as provided.

Versioned JSON caches are separated by access context. The old local pickle cache is read only to migrate this app's existing data. Keep cache files private and never substitute files from untrusted sources. Filters and seen history are local to the installation, as in the original app; this is not a multi-account preference service.

## Optional: first-access listings

To include first-access listings, create a local (gitignored) auth file:

```bash
# .listings_auth.json — do not commit this file
{
  "cookie_header": "your-session-cookie-here"
}
```

Copy the cookie header from a logged-in browser session on listingsproject.com. If first-access discovery fails, refresh details will ask you to check your membership/session. Public regions can still refresh.

**Never commit `.listings_auth.json`** — it is already in `.gitignore`.

## macOS launcher

Double-click `ListingProject Explorer.app` to activate the project venv, start Streamlit on port 8501, and open the browser. Requires a local `.venv` already set up (see Setup above).

To regenerate the app icon from a square PNG:

```bash
./scripts/set-app-icon.sh path/to/icon.png
```

## Troubleshooting

- If `streamlit` is not found, make sure the virtual environment is activated: `source .venv/bin/activate`
- If dependencies are missing, reinstall: `pip install -r requirements.txt`
- First-access refresh warning → check membership and update `.listings_auth.json` with a fresh cookie

## Verification

Run `.venv/bin/python -m unittest discover -s tests -v` for parser, cache, migration, and UI behavior checks.

## License

MIT — see [LICENSE](LICENSE).
