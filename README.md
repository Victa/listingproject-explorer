# Listings Project

Personal Streamlit app for searching NYC listings on [listingsproject.com](https://www.listingsproject.com).

Fetches the public NYC index once, caches it, then filters in memory — no re-crawl when you change filters.

## Demo

Try the live demo: [listings-projects.streamlit.app](https://listings-projects.streamlit.app/)

Note: the demo runs with public access only. If you have a listingsproject.com membership and want to use your personal token for first-access listings, you'll need to [run the app locally](#setup) and [set up your token](#optional-first-access-listings).

## Features

- **Borough, neighborhood, and property-type filters** — multi-select popovers; neighborhoods are canonicalized (e.g. "Bed Stuy" → "Bedford-Stuyvesant")
- **Date availability** — any dates, exact check-in/check-out (with optional ± flexibility), or flexible week/month stays across selected months
- **New listings** — highlights listings you haven't seen yet across refreshes
- **First access** — optional (requires a session cookie; see below)
- **CSV export** of the filtered results
- **macOS launcher** — double-click `Listings Project.app` to start Streamlit and open the browser

## Requirements

- Python 3.10+
- Internet access (the app fetches from listingsproject.com)

## Setup

```bash
git clone <repo-url>
cd ListingProject
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Then open the local URL Streamlit prints (usually `http://localhost:8501`).

Listings are cached for 6 hours (in memory and on disk). Click **Refresh listings** in the sidebar to force a fresh crawl.

## Optional: first-access listings

To include first-access listings, create a local (gitignored) auth file:

```bash
# .listings_auth.json — do not commit this file
{
  "cookie_header": "your-session-cookie-here"
}
```

Copy the cookie header from a logged-in browser session on listingsproject.com. If the cookie expires, the sidebar will warn you to update the file.

**Never commit `.listings_auth.json`** — it is already in `.gitignore`.

## macOS launcher

Double-click `Listings Project.app` to activate the project venv, start Streamlit on port 8501, and open the browser. Requires a local `.venv` already set up (see Setup above).

To regenerate the app icon from a square PNG:

```bash
./scripts/set-app-icon.sh path/to/icon.png
```

## Troubleshooting

- If `streamlit` is not found, make sure the virtual environment is activated: `source .venv/bin/activate`
- If dependencies are missing, reinstall: `pip install -r requirements.txt`
- First-access shows no results / sidebar warning → update `.listings_auth.json` with a fresh cookie

## License

MIT — see [LICENSE](LICENSE).
