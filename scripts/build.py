#!/usr/bin/env python3
"""
Social Housing California — build script.

Fetches four tabs from the published Google Sheet as CSV, parses them,
and renders the content into index.html and map.html by replacing
marker comments in the templates.

Marker pattern in templates:
    <!-- BUILD:section_name:START -->
    ...generated HTML...
    <!-- BUILD:section_name:END -->

Anything between matching START/END markers is replaced on each build.
"""

import csv
import io
import os
import re
import sys
from datetime import datetime
from html import escape
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"

SHEET_ID = os.environ.get("SHEET_ID")
if not SHEET_ID:
    sys.exit("ERROR: SHEET_ID env var not set")

# GIDs for each tab — these are set as GitHub secrets OR derived
# from the published CSV URL pattern. Using sheet name in the URL
# is more readable than GIDs and survives tab reordering.
TABS = {
    "page_copy": "page_copy",
    "map_locations": "map_locations",
    "partners": "partners",
    "resources": "resources",
}

CSV_URL = (
    "https://docs.google.com/spreadsheets/d/{sid}/gviz/tq"
    "?tqx=out:csv&sheet={tab}&headers=1"
)


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_tab(tab_name: str) -> list[dict]:
    """Fetch one tab as a list of dicts. Handles BOM and whitespace in headers."""
    url = CSV_URL.format(sid=SHEET_ID, tab=tab_name)
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    # Strip UTF-8 BOM if Google included one
    text = r.text
    if text.startswith("\ufeff"):
        text = text[1:]
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        # Normalize header keys: strip whitespace, lowercase
        cleaned = {}
        for k, v in row.items():
            if k is None:
                continue
            nk = k.strip().lower()
            cleaned[nk] = v.strip() if isinstance(v, str) else v
        rows.append(cleaned)
    print(f"  {tab_name}: {len(rows)} rows, headers={reader.fieldnames}")
    return rows


def is_active(row: dict) -> bool:
    """A row is active unless active column is explicitly FALSE/no/0."""
    val = (row.get("active") or "").strip().lower()
    if val in ("false", "no", "0", "n"):
        return False
    return True


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_page_copy(rows: list[dict]) -> dict[str, str]:
    """page_copy tab is a key/value lookup."""
    result = {r["key"]: r.get("value", "") for r in rows if r.get("key")}
    print(f"  page_copy loaded {len(result)} keys: {sorted(result.keys())}")
    return result


def render_resources(rows: list[dict]) -> str:
    """Render Resources cards (reuses .brief-card CSS), sorted by date desc."""
    active = [r for r in rows if is_active(r)]

    def parse_date(s: str):
        try:
            return datetime.strptime(s.strip(), "%Y-%m-%d")
        except (ValueError, AttributeError):
            return datetime.min

    active.sort(key=lambda r: parse_date(r.get("date", "")), reverse=True)

    if not active:
        return '<p class="empty">Resources coming soon.</p>'

    cards = []
    for r in active:
        title = escape(r.get("title", "").strip())
        desc = escape(r.get("description", "").strip())
        link = r.get("link", "").strip()
        category = escape(r.get("category", "").strip())
        date = r.get("date", "").strip()

        if not (title and link):
            continue

        date_display = ""
        if date:
            try:
                date_display = datetime.strptime(date, "%Y-%m-%d").strftime("%b %Y")
            except ValueError:
                date_display = date

        cards.append(f'''      <a href="{escape(link)}" class="brief-card reveal"
         target="_blank" rel="noopener">
        <div class="brief-card-top">
          <div class="brief-meta-row">
            <span class="brief-label">{category}</span>
            <span class="brief-date">{date_display}</span>
          </div>
          <div class="brief-title">{title}</div>
        </div>
        <div class="brief-card-body">
          <p class="brief-excerpt">{desc}</p>
          <span class="brief-read">Read &rarr;</span>
        </div>
      </a>''')

    return "\n".join(cards)


def render_partners(rows: list[dict]) -> str:
    """Render the Partners section (reuses .partner-cell CSS)."""
    active = [r for r in rows if is_active(r)]
    if not active:
        return '<p class="empty">Partners coming soon.</p>'

    items = []
    for r in active:
        name = escape(r.get("name", "").strip())
        url = r.get("url", "").strip()
        logo_url = r.get("logo_url", "").strip()
        if not name:
            continue

        inner = (
            f'<img src="{escape(logo_url)}" alt="{name}" loading="lazy">'
            if logo_url
            else name
        )

        if url:
            items.append(
                f'      <a class="partner-cell" href="{escape(url)}" '
                f'target="_blank" rel="noopener" title="{name}">{inner}</a>'
            )
        else:
            items.append(f'      <div class="partner-cell" title="{name}">{inner}</div>')

    return "\n".join(items)


def render_map_data(rows: list[dict]) -> str:
    """Render map locations as a JSON blob for map.html to consume."""
    import json

    active = [r for r in rows if is_active(r)]
    locations = []
    for r in active:
        try:
            lat = float(r.get("lat", "") or 0)
            lng = float(r.get("lng", "") or 0)
        except ValueError:
            continue
        if lat == 0 and lng == 0:
            continue

        locations.append({
            "id": r.get("id", "").strip(),
            "type": r.get("type", "").strip().lower(),
            "name": r.get("name", "").strip(),
            "city": r.get("city", "").strip(),
            "region": r.get("region", "").strip(),
            "lat": lat,
            "lng": lng,
            "desc": r.get("desc", "").strip(),
            "units": r.get("units", "").strip(),
            "url": r.get("url", "").strip(),
        })

    return "    const LOCATIONS = " + json.dumps(locations, indent=2) + ";"


# ---------------------------------------------------------------------------
# Template injection
# ---------------------------------------------------------------------------

MARKER_RE = re.compile(
    r"(<!-- BUILD:(?P<name>[a-z_]+):START -->)"
    r"(?P<content>.*?)"
    r"(<!-- BUILD:(?P=name):END -->)",
    re.DOTALL,
)


def inject(template_html: str, sections: dict[str, str]) -> str:
    """Replace every BUILD block with its rendered content."""
    def sub(match: re.Match) -> str:
        name = match.group("name")
        start = match.group(1)
        end = match.group(4)
        if name in sections:
            return f"{start}\n{sections[name]}\n      {end}"
        print(f"  WARN: no content provided for section '{name}'")
        return match.group(0)

    return MARKER_RE.sub(sub, template_html)


def inject_page_copy(html: str, copy: dict[str, str]) -> str:
    """Replace {{page_copy.key}} placeholders with values."""
    def sub(match: re.Match) -> str:
        key = match.group(1).strip()
        return escape(copy.get(key, f"[missing: {key}]"))

    return re.sub(r"\{\{\s*page_copy\.([a-z_0-9]+)\s*\}\}", sub, html)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("Fetching Sheets tabs...")
    data = {name: fetch_tab(tab) for name, tab in TABS.items()}

    print("Rendering sections...")
    copy = render_page_copy(data["page_copy"])
    sections_index = {
        "resources": render_resources(data["resources"]),
        "partners": render_partners(data["partners"]),
    }
    sections_map = {
        "map_data": render_map_data(data["map_locations"]),
    }

    # index.html
    print("Building index.html...")
    tpl_index = (TEMPLATES / "index.html").read_text(encoding="utf-8")
    out_index = inject(tpl_index, sections_index)
    out_index = inject_page_copy(out_index, copy)
    (ROOT / "index.html").write_text(out_index, encoding="utf-8")

    # map.html
    print("Building map.html...")
    tpl_map = (TEMPLATES / "map.html").read_text(encoding="utf-8")
    out_map = inject(tpl_map, sections_map)
    out_map = inject_page_copy(out_map, copy)
    (ROOT / "map.html").write_text(out_map, encoding="utf-8")

    print("Done.")


if __name__ == "__main__":
    main()
