# Listings Project

Streamlit app for searching Listings Project NYC results.

## Requirements

- Python 3.10+ (project uses a local virtual environment)
- Internet access (the app fetches listings data from listingsproject.com)

## Run

From the project root:

```bash
cd /Users/vcoulon2/Projects/ListingProject
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Then open the local URL Streamlit prints (usually `http://localhost:8501`).

## Troubleshooting

- If `streamlit` is not found, make sure the virtual environment is activated:
  `source .venv/bin/activate`
- If dependencies are missing, reinstall:
  `pip install -r requirements.txt`
