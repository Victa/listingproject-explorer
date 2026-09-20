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
  --lp-bg: #ffffff;
  --lp-surface: #ffffff;
  --lp-text: #000000;
  --lp-text-secondary: #6b6b6b;
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
  background-color: var(--lp-bg) !important;
  color: var(--lp-text);
}

.stApp [data-testid="stAppViewContainer"],
.stApp [data-testid="stHeader"],
.stApp [data-testid="stToolbar"] {
  background: var(--lp-bg) !important;
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
  max-width: 1280px;
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

/* Keep the refresh action in view while the filters and status scroll. */
section[data-testid="stSidebar"] [data-testid="stSidebarContent"] {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
section[data-testid="stSidebar"] [data-testid="stSidebarHeader"] {
  flex-shrink: 0;
}
section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {
  flex: 1;
  min-height: 0;
  padding-bottom: 0;
}
section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] > div,
section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] > div > [data-testid="stVerticalBlock"] {
  height: 100%;
  min-height: 0;
}
section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] > div > [data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"]:has(.st-key-sidebar_filters) {
  flex: 1 1 0;
  min-height: 0;
  overflow: hidden;
}
section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] > div > [data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"]:has(.st-key-sidebar_footer) {
  flex: 0 0 auto;
}
section[data-testid="stSidebar"] .st-key-sidebar_filters {
  flex: 1 1 0;
  height: 100%;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding-bottom: 1rem;
}
section[data-testid="stSidebar"] .st-key-sidebar_footer {
  flex: 0 0 auto;
  border-top: 1px solid var(--lp-border);
  background: var(--lp-surface);
  padding: 1rem 0 max(1rem, env(safe-area-inset-bottom));
}

/* —— Results grid: continuous CSS grid (1 / 2 / 3 by breakpoint) —— */
div[class*="st-key-listings_grid"] {
  display: grid !important;
  grid-template-columns: 1fr !important;
  gap: 1.5rem !important;
  width: 100% !important;
  align-items: start;
}

/* Flatten Streamlit chrome so listing cards become the grid items */
div[class*="st-key-listings_grid"] [data-testid="stVerticalBlockBorderWrapper"],
div[class*="st-key-listings_grid"] [data-testid="stVerticalBlock"]:not([class*="st-key-new_card_"]):not([class*="st-key-seen_card_"]),
div[class*="st-key-listings_grid"] [data-testid="stElementContainer"]:has(div[class*="st-key-new_card_"]),
div[class*="st-key-listings_grid"] [data-testid="stElementContainer"]:has(div[class*="st-key-seen_card_"]) {
  display: contents !important;
}

div[class*="st-key-listings_grid"] div[class*="st-key-new_card_"],
div[class*="st-key-listings_grid"] div[class*="st-key-seen_card_"] {
  min-width: 0 !important;
  width: 100% !important;
  max-width: 100% !important;
}

@media (min-width: 700px) {
  div[class*="st-key-listings_grid"] {
    grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
  }
}

@media (min-width: 1000px) {
  div[class*="st-key-listings_grid"] {
    grid-template-columns: repeat(3, minmax(0, 1fr)) !important;
  }
}

/* —— Listing cards (keyed containers in Streamlit 1.56+) —— */
div[class*="st-key-new_card_"],
div[class*="st-key-seen_card_"] {
  position: relative !important;
  container-type: inline-size;
  width: 100% !important;
  max-width: 100% !important;
  background: transparent !important;
  border: none !important;
  box-shadow: none !important;
  border-radius: 0 !important;
  padding: 0 !important;
  margin: 0 !important;
  overflow: visible !important;
  height: 100%;
  /* Card IS the vertical block — kill Streamlit’s ~1rem flex gap here */
  gap: 0 !important;
  row-gap: 0 !important;
  column-gap: 0 !important;
}

/* Collapse Streamlit’s default vertical rhythm inside each tile */
div[class*="st-key-new_card_"] [data-testid="stVerticalBlockBorderWrapper"],
div[class*="st-key-seen_card_"] [data-testid="stVerticalBlockBorderWrapper"],
div[class*="st-key-new_card_"] [data-testid="stVerticalBlock"],
div[class*="st-key-seen_card_"] [data-testid="stVerticalBlock"] {
  padding: 0 !important;
  margin: 0 !important;
  gap: 0 !important;
  row-gap: 0 !important;
}

div[class*="st-key-new_card_"] > div,
div[class*="st-key-seen_card_"] > div,
div[class*="st-key-new_card_"] [data-testid="stElementContainer"],
div[class*="st-key-seen_card_"] [data-testid="stElementContainer"],
div[class*="st-key-new_card_"] [data-testid="stMarkdownContainer"],
div[class*="st-key-seen_card_"] [data-testid="stMarkdownContainer"],
div[class*="st-key-new_card_"] [data-testid="stMarkdown"],
div[class*="st-key-seen_card_"] [data-testid="stMarkdown"],
div[class*="st-key-new_card_"] [data-testid="stCustomComponentV1"],
div[class*="st-key-seen_card_"] [data-testid="stCustomComponentV1"] {
  padding: 0 !important;
  margin: 0 !important;
}

div[class*="st-key-new_card_"] [data-testid="stMarkdownContainer"] p,
div[class*="st-key-seen_card_"] [data-testid="stMarkdownContainer"] p {
  margin: 0 !important;
  padding: 0 !important;
}

div[class*="st-key-new_card_"] [data-testid="stCustomComponentV1"],
div[class*="st-key-seen_card_"] [data-testid="stCustomComponentV1"] {
  position: relative !important;
  width: 100% !important;
  aspect-ratio: 3 / 2 !important;
  height: auto !important;
  min-height: 0 !important;
  padding: 0 !important;
  margin: 0 !important;
}

div[class*="st-key-new_card_"] iframe,
div[class*="st-key-seen_card_"] iframe {
  position: absolute !important;
  inset: 0 !important;
  z-index: 2;
  border: none !important;
  border-radius: var(--lp-radius-image) !important;
  display: block;
  margin: 0 !important;
  width: 100% !important;
  height: 100% !important;
  overflow: hidden;
}

div[class*="st-key-new_card_"] [data-testid="stImage"],
div[class*="st-key-seen_card_"] [data-testid="stImage"] {
  border-radius: var(--lp-radius-image) !important;
  display: block;
  margin: 0 !important;
}

div[class*="st-key-new_card_"] img,
div[class*="st-key-seen_card_"] img {
  border-radius: var(--lp-radius-image) !important;
  object-fit: cover;
}

.lp-photo-placeholder {
  position: relative;
  z-index: 2;
  width: 100%;
  aspect-ratio: 3 / 2;
  background: var(--lp-border);
  border-radius: var(--lp-radius-image);
}

/* Full-tile click target over text; photo iframes stack above */
.lp-card-hit {
  position: absolute;
  inset: 0;
  z-index: 1;
  border-radius: 0;
  cursor: pointer;
}

/* Card body — tight stack matching the mockup */
.lp-card-body {
  position: relative;
  z-index: 0;
  padding: 0.5rem 0 0 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  pointer-events: none;
}

.lp-card-title {
  font-size: 1.05rem;
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.3;
  color: #000000 !important;
  text-decoration: none !important;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.lp-card-meta,
.lp-card-dates {
  font-size: 0.875rem;
  font-weight: 400;
  line-height: 1.35;
  color: #6b6b6b;
}

.lp-card-dates-length {
  font-weight: 400;
  color: inherit;
}

.lp-card-dates-sep {
  color: inherit;
  font-weight: 400;
}

.lp-card-price {
  margin-top: 0.35rem;
  font-size: 0.95rem;
  line-height: 1.35;
}

.lp-price-amount {
  font-weight: 700;
  color: #000000;
  text-decoration: underline;
  text-underline-offset: 2px;
}

.lp-price-period {
  font-weight: 400;
  color: #6b6b6b;
  text-decoration: none;
}

.lp-card-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  margin-top: 0.45rem;
}

.lp-badge {
  display: inline-block;
  padding: 0.2rem 0.65rem;
  border-radius: var(--lp-radius-pill);
  font-size: 0.75rem;
  font-weight: 500;
  letter-spacing: -0.01em;
  line-height: 1.3;
}

.lp-badge-access {
  background: #f0f0f2;
  color: #6b6b6b;
  font-weight: 500;
}

.lp-badge-new {
  background: var(--lp-accent-tint);
  color: var(--lp-accent);
}

/* —— Numbered results pagination —— */
div[class*="st-key-results_pagination"] {
  display: flex !important;
  justify-content: center !important;
  margin: 1.75rem 0 0.5rem !important;
}

div[class*="st-key-results_pagination"] [data-testid="stHorizontalBlock"] {
  display: flex !important;
  justify-content: center !important;
  align-items: center !important;
  gap: 0.15rem !important;
  width: auto !important;
  max-width: 100% !important;
  margin: 0 auto !important;
}

div[class*="st-key-results_pagination"] [data-testid="stColumn"] {
  flex: 0 0 auto !important;
  width: auto !important;
  min-width: 0 !important;
}

div[class*="st-key-results_pagination"] [data-testid="stElementContainer"],
div[class*="st-key-results_pagination"] [data-testid="stMarkdownContainer"] {
  width: auto !important;
}

div[class*="st-key-results_pagination"] .stButton > button {
  min-width: 2.25rem !important;
  width: 2.25rem !important;
  height: 2.25rem !important;
  padding: 0 !important;
  border-radius: 50% !important;
  border: none !important;
  box-shadow: none !important;
  background: transparent !important;
  color: var(--lp-text) !important;
  font-size: 0.95rem !important;
  font-weight: 500 !important;
  line-height: 1 !important;
}

div[class*="st-key-results_pagination"] .stButton > button:hover:not(:disabled) {
  background: rgba(0, 0, 0, 0.05) !important;
  box-shadow: none !important;
  transform: none !important;
}

div[class*="st-key-results_pagination"] .stButton > button[kind="primary"],
div[class*="st-key-results_pagination"] .stButton > button[kind="primary"]:disabled {
  background: #1d1d1f !important;
  color: #ffffff !important;
  opacity: 1 !important;
  cursor: default !important;
}

div[class*="st-key-results_pagination"] .stButton > button:disabled:not([kind="primary"]) {
  color: #c7c7cc !important;
  background: transparent !important;
  opacity: 1 !important;
}

div[class*="st-key-results_pagination"] .lp-page-ellipsis {
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 2.25rem;
  height: 2.25rem;
  color: var(--lp-text);
  font-size: 0.95rem;
  font-weight: 500;
  line-height: 1;
  user-select: none;
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
/* Refresh feedback stays subtle and honors the system motion preference. */
@keyframes lp-arrive {
  from { opacity: .45; }
  to { opacity: 1; }
}
@keyframes lp-new-highlight {
  from { box-shadow: 0 0 0 3px rgba(13, 148, 136, .35); }
  to { box-shadow: var(--lp-shadow); }
}
div[class*="st-key-seen_card_"] { animation: lp-arrive .24s ease-out; }
div[class*="st-key-new_card_"] { animation: lp-arrive .24s ease-out, lp-new-highlight 1.6s ease-out; }
.lp-loading-skeleton {
  height: 220px;
  border-radius: var(--lp-radius-card);
  background: linear-gradient(100deg, #f5f5f7 30%, #eaecef 50%, #f5f5f7 70%);
  background-size: 200% 100%;
  animation: lp-skeleton 1.8s ease-in-out infinite;
}
@keyframes lp-skeleton { to { background-position: -200% 0; } }
@media (prefers-reduced-motion: reduce) {
  div[class*="st-key-new_card_"], div[class*="st-key-seen_card_"], .lp-loading-skeleton {
    animation: none !important;
    transition: none !important;
  }
}
</style>
"""


def inject_theme() -> None:
    """Inject the Apple-inspired design-system CSS once per page render."""
    st.markdown(_THEME_CSS, unsafe_allow_html=True)
