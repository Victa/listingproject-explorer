"""
Apple-inspired CSS design system for the Streamlit UI.

Call ``inject_theme()`` once after ``st.set_page_config``.
"""

from __future__ import annotations

import streamlit as st

_THEME_CSS = """
<style>
:root {
  --lp-font: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI",
    Roboto, Helvetica, Arial, sans-serif;
  --lp-bg: #f5f5f7;
  --lp-surface: #ffffff;
  --lp-text: #1d1d1f;
  --lp-text-secondary: #6e6e73;
  --lp-accent: #0071e3;
  --lp-accent-hover: #0077ed;
  --lp-accent-tint: rgba(0, 113, 227, 0.12);
  --lp-teal: #0d9488;
  --lp-teal-tint: rgba(13, 148, 136, 0.12);
  --lp-border: #e5e5ea;
  --lp-hairline: rgba(0, 0, 0, 0.06);
  --lp-radius-card: 18px;
  --lp-radius-image: 14px;
  --lp-radius-pill: 999px;
  --lp-radius-control: 12px;
  --lp-shadow: 0 1px 2px rgba(0, 0, 0, 0.05), 0 10px 30px rgba(0, 0, 0, 0.05);
  --lp-shadow-hover: 0 2px 4px rgba(0, 0, 0, 0.06), 0 16px 40px rgba(0, 0, 0, 0.08);
  --lp-space-1: 0.25rem;
  --lp-space-2: 0.5rem;
  --lp-space-3: 0.75rem;
  --lp-space-4: 1rem;
  --lp-space-5: 1.5rem;
}

html, body, [class*="css"], .stApp {
  font-family: var(--lp-font) !important;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

.stApp {
  background-color: var(--lp-bg);
  color: var(--lp-text);
}

/* —— Typography —— */
h1, h2, h3, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {
  font-family: var(--lp-font) !important;
  letter-spacing: -0.02em;
  font-weight: 600 !important;
  color: var(--lp-text) !important;
}

[data-testid="stCaptionContainer"],
.stCaption {
  color: var(--lp-text-secondary) !important;
}

/* —— Main content breathing room —— */
.block-container {
  padding-top: 2rem !important;
  padding-bottom: 3rem !important;
  max-width: 1120px;
}

/* —— Sidebar: frosted glass —— */
section[data-testid="stSidebar"] {
  background: rgba(255, 255, 255, 0.72) !important;
  backdrop-filter: saturate(180%) blur(20px);
  -webkit-backdrop-filter: saturate(180%) blur(20px);
  border-right: 1px solid var(--lp-hairline) !important;
}

section[data-testid="stSidebar"] > div {
  background: transparent !important;
}

section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3 {
  letter-spacing: -0.015em;
}

/* —— Listing cards (keyed bordered containers in Streamlit 1.56+) —— */
div[class*="st-key-new_card_"],
div[class*="st-key-seen_card_"] {
  background: var(--lp-surface) !important;
  border: 1px solid var(--lp-hairline) !important;
  border-radius: var(--lp-radius-card) !important;
  box-shadow: var(--lp-shadow) !important;
  padding: var(--lp-space-4) !important;
  margin-bottom: var(--lp-space-4) !important;
  transition: box-shadow 0.2s ease, transform 0.2s ease;
}

div[class*="st-key-new_card_"]:hover,
div[class*="st-key-seen_card_"]:hover {
  box-shadow: var(--lp-shadow-hover) !important;
  transform: translateY(-1px);
}

/* Card images */
div[class*="st-key-new_card_"] img,
div[class*="st-key-seen_card_"] img {
  border-radius: var(--lp-radius-image) !important;
  object-fit: cover;
}

/* Lazy gallery “next” control — first click fetches detail-page photos */
div[class*="st-key-gal_next_"] {
  position: relative;
  margin-top: -3.25rem;
  margin-bottom: 1.5rem;
  display: flex !important;
  justify-content: flex-end;
  padding-right: 0.5rem;
  z-index: 2;
}

div[class*="st-key-gal_next_"] button {
  border-radius: var(--lp-radius-pill) !important;
  min-width: 28px !important;
  width: 28px !important;
  height: 28px !important;
  padding: 0 !important;
  font-size: 1.1rem !important;
  line-height: 1 !important;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.12) !important;
  background: rgba(255, 255, 255, 0.92) !important;
  border: 1px solid var(--lp-border) !important;
  color: var(--lp-text) !important;
}

div[class*="st-key-gal_next_"] button:hover {
  background: #fff !important;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.14) !important;
}

/* —— Buttons / link buttons —— */
.stButton > button,
.stDownloadButton > button,
.stLinkButton > a {
  border-radius: var(--lp-radius-pill) !important;
  font-weight: 500 !important;
  letter-spacing: -0.01em;
  transition: background-color 0.15s ease, box-shadow 0.15s ease, transform 0.1s ease !important;
}

.stButton > button[kind="primary"],
.stDownloadButton > button[kind="primary"],
.stLinkButton > a {
  background-color: var(--lp-accent) !important;
  border-color: var(--lp-accent) !important;
  color: #fff !important;
}

.stButton > button[kind="primary"]:hover,
.stDownloadButton > button[kind="primary"]:hover,
.stLinkButton > a:hover {
  background-color: var(--lp-accent-hover) !important;
  border-color: var(--lp-accent-hover) !important;
  box-shadow: 0 4px 14px rgba(0, 113, 227, 0.28);
}

.stButton > button[kind="secondary"],
.stDownloadButton > button[kind="secondary"] {
  background-color: var(--lp-surface) !important;
  border: 1px solid var(--lp-border) !important;
  color: var(--lp-text) !important;
}

.stButton > button[kind="secondary"]:hover,
.stDownloadButton > button[kind="secondary"]:hover {
  background-color: var(--lp-bg) !important;
}

/* —— Inputs, selects, popovers —— */
div[data-testid="stTextInput"] input,
div[data-testid="stNumberInput"] input,
div[data-testid="stDateInput"] input,
div[data-baseweb="select"] > div,
div[data-baseweb="input"] {
  border-radius: var(--lp-radius-control) !important;
  border-color: var(--lp-border) !important;
}

div[data-testid="stTextInput"] input:focus,
div[data-testid="stNumberInput"] input:focus,
div[data-testid="stDateInput"] input:focus {
  border-color: var(--lp-accent) !important;
  box-shadow: 0 0 0 3px var(--lp-accent-tint) !important;
}

/* Popovers (filter dropdowns) */
div[data-testid="stPopover"] button {
  border-radius: var(--lp-radius-control) !important;
  border: 1px solid var(--lp-border) !important;
  background: var(--lp-surface) !important;
}

/* Radio groups feel more Apple-segmented */
div[role="radiogroup"] label {
  border-radius: var(--lp-radius-pill) !important;
}

/* Checkboxes: slightly larger hit targets */
div[data-testid="stCheckbox"] label {
  font-size: 0.95rem;
}

/* Multiselect tags as pills */
span[data-baseweb="tag"] {
  border-radius: var(--lp-radius-pill) !important;
  background-color: var(--lp-accent-tint) !important;
  color: var(--lp-accent) !important;
}

/* Progress / spinner polish */
div[data-testid="stProgressBar"] > div > div {
  background-color: var(--lp-accent) !important;
}

/* Info / warning / error boxes */
div[data-testid="stAlert"] {
  border-radius: var(--lp-radius-control) !important;
}
</style>
"""


def inject_theme() -> None:
    """Inject the Apple-inspired design-system CSS once per page render."""
    st.markdown(_THEME_CSS, unsafe_allow_html=True)
