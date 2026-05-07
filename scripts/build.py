#!/usr/bin/env python3
"""
Social Housing California — build script (v2).

How the build is gated
----------------------
The TOP of the `page_copy` tab holds reserved control keys. The build runs
only when both of these are true:

  - publish        == "yes"
  - publish_token  != the value stored in .build-state.json

Otherwise it exits immediately after one HTTP request — keeps idle cron
cycles cheap.

Reserved keys (filtered out before page copy is rendered):
  publish          yes/no — master switch
  publish_token    any string — bump to publish (date, counter, "v17", ...)
  note             optional, free text, logged on each publish

How the build edits files
-------------------------
index.html and map.html ARE the source of truth AND the deployed files.
There is no separate templates/ directory. The build only rewrites content
between BUILD markers; everything outside the markers — CSS, nav, scripts,
manual edits — is preserved across builds.

  Marker pattern in HTML files:
    <!-- BUILD:section_name:START -->
    ...generated HTML (replaced on each publish)...
    <!-- BUILD:section_name:END -->

  Page-copy placeholders (anywhere in the file):
    {{page_copy.some_key}}

Section-level diffing
---------------------
Each rendered section is compared to what's already in the file. Files are
only written if at least one section actually changed. Git commits show
only the real diffs.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
from datetime import datetime, timezone
from html import escape
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Paths and config
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / ".build-state.json"

INDEX_PATH = ROOT / "index.html"
MAP_PATH = ROOT / "map.html"

SHEET_ID = os.environ.get("SHEET_ID")
if not SHEET_ID:
    sys.exit("ERROR: SHEET_ID env var not set")

# gviz CSV endpoint. &headers=1 forces single-row header detection — without
# it, gviz auto-detects multi-row headers on text-heavy tabs and silently
# drops data (we hit this on page_copy in the v1 build).
CSV_URL = (
    "https://docs.google.com/spreadsheets/d/{sid}/gviz/tq"
    "?tqx=out:csv&sheet={tab}&headers=1"
)

# Tabs we read. Add a new section by adding a key here, BUILD markers in
# the HTML, and a render_* function below.
CONTENT_TABS = {
    "page_copy": "page_copy",
    "map_locations": "map_locations",
    "partners": "partners",
    "resources": "resources",
    "stories": "stories",
}

# Keys reserved at the top of page_copy for build control. These never
# render as placeholders in the site — they're filtered out before page
# copy is used for rendering, so they can't collide with any
# {{page_copy.X}} placeholder anywhere in the HTML.
RESERVED_KEYS = {"publish", "publish_token", "note"}


# ---------------------------------------------------------------------------
# Sheet fetch
# ---------------------------------------------------------------------------

def fetch_tab(tab_name: str) -> list[dict]:
    """Fetch one tab as a list of dicts. Strips BOM, normalizes header keys."""
    url = CSV_URL.format(sid=SHEET_ID, tab=tab_name)
    r = requests.get(url, timeout=30)
    r.raise_for_status()

    text = r.text
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")

    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        # Lowercase headers, strip whitespace, drop None keys.
        clean = {}
        for k, v in row.items():
            if k is None:
                continue
            nk = k.strip().lower()
            clean[nk] = v.strip() if isinstance(v, str) else (v or "")
        if any(clean.values()):  # skip fully blank rows
            rows.append(clean)
    print(f"  {tab_name}: {len(rows)} rows")
    return rows


def is_active(row: dict) -> bool:
    """A row is active unless its `active` column is FALSE/no/0/n."""
    val = (row.get("active") or "").strip().lower()
    return val not in ("false", "no", "0", "n")


def parse_date(s: str) -> datetime:
    """Parse YYYY-MM-DD; return datetime.min on failure (sorts oldest)."""
    try:
        return datetime.strptime((s or "").strip(), "%Y-%m-%d")
    except (ValueError, AttributeError):
        return datetime.min


# ---------------------------------------------------------------------------
# State (last-built token tracking)
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except json.JSONDecodeError:
            print("  WARNING: .build-state.json malformed, treating as empty")
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


# ---------------------------------------------------------------------------
# Page copy
# ---------------------------------------------------------------------------

def split_page_copy(rows: list[dict]) -> tuple[dict[str, str], dict[str, str]]:
    """Return (control_dict, content_dict).

    Reserved keys land in control_dict and never appear in content_dict, so
    {{page_copy.publish}} can't accidentally render somewhere.
    """
    control: dict[str, str] = {}
    content: dict[str, str] = {}
    for r in rows:
        key = (r.get("key") or "").strip()
        if not key:
            continue
        value = r.get("value", "")
        if key in RESERVED_KEYS:
            control[key] = value
        else:
            content[key] = value
    return control, content


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def render_partners(rows: list[dict]) -> str:
    """Render Partners (uses .partner-cell CSS).

    `logo_url` may be a relative path (images/partners/acce.png) or full URL.
    Empty `logo_url` → text-only fallback card.
    """
    active = [r for r in rows if is_active(r)]
    if not active:
        return '      <p class="empty">Partners coming soon.</p>'

    items = []
    for r in active:
        name = r.get("name", "").strip()
        url = r.get("url", "").strip()
        logo_url = r.get("logo_url", "").strip()
        if not name:
            continue

        name_e = escape(name)
        inner = (
            f'<img src="{escape(logo_url)}" alt="{name_e}" loading="lazy">'
            if logo_url
            else name_e
        )

        if url:
            items.append(
                f'      <a class="partner-cell" href="{escape(url)}" '
                f'target="_blank" rel="noopener" title="{name_e}">{inner}</a>'
            )
        else:
            items.append(
                f'      <div class="partner-cell" title="{name_e}">{inner}</div>'
            )

    return "\n".join(items)


def render_resources(rows: list[dict]) -> str:
    """Render Library/Resources cards (uses .brief-card CSS), sorted by date desc."""
    active = [r for r in rows if is_active(r)]
    active.sort(key=lambda r: parse_date(r.get("date", "")), reverse=True)

    if not active:
        return '      <p class="empty">Resources coming soon.</p>'

    cards = []
    for r in active:
        title = r.get("title", "").strip()
        desc = r.get("description", "").strip()
        link = r.get("link", "").strip()
        category = r.get("category", "").strip()
        date = r.get("date", "").strip()

        if not (title and link):
            continue

        date_display = ""
        if date:
            try:
                date_display = datetime.strptime(date, "%Y-%m-%d").strftime("%b %Y")
            except ValueError:
                date_display = date

        cards.append(
            f'      <a href="{escape(link)}" class="brief-card reveal" '
            f'target="_blank" rel="noopener">\n'
            f'        <div class="brief-card-top">\n'
            f'          <div class="brief-meta-row">\n'
            f'            <span class="brief-label">{escape(category)}</span>\n'
            f'            <span class="brief-date">{escape(date_display)}</span>\n'
            f'          </div>\n'
            f'          <div class="brief-title">{escape(title)}</div>\n'
            f'        </div>\n'
            f'        <div class="brief-card-body">\n'
            f'          <p class="brief-excerpt">{escape(desc)}</p>\n'
            f'          <span class="brief-read">Read &rarr;</span>\n'
            f'        </div>\n'
            f'      </a>'
        )

    return "\n".join(cards)


YOUTUBE_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?v=|embed/|v/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)


def youtube_id(url: str) -> str | None:
    """Return the YouTube video ID if `url` is a YouTube link, else None."""
    if not url:
        return None
    m = YOUTUBE_RE.search(url)
    return m.group(1) if m else None


def render_stories(rows: list[dict]) -> str:
    """Render Stories/News/Campaigns cards. Sorted by date desc; missing
    dates land at the bottom but preserve their original order.

    Schema:
      title, organization, category, date, media_url, body, link_url, active

    Auto-detects YouTube embeds in `media_url`. Anything else in `media_url`
    is treated as an image. `category` (news / story / campaign) drives the
    filter pills via `data-category` and the small badge in the corner.
    """
    active = [r for r in rows if is_active(r)]
    # Stable sort: newest first, undated to bottom.
    active.sort(
        key=lambda r: (parse_date(r.get("date", "")) or datetime.min),
        reverse=True,
    )

    if not active:
        return '      <p class="empty">Stories coming soon.</p>'

    cards = []
    for i, r in enumerate(active):
        title = r.get("title", "").strip()
        org = r.get("organization", "").strip()
        category = (r.get("category", "").strip() or "story").lower()
        media_url = r.get("media_url", "").strip()
        body = r.get("body", "").strip()
        link_url = r.get("link_url", "").strip()
        date = r.get("date", "").strip()

        if not title:
            continue

        # Category must be one of three known values; default to story.
        if category not in ("news", "story", "campaign"):
            category = "story"

        # Reveal delay class staggers entrance animations on the page.
        reveal = "reveal" + (f" reveal-delay-{(i % 3) + 1}" if i else "")

        # Media block.
        media_html = ""
        yt_id = youtube_id(media_url)
        if yt_id:
            media_html = (
                f'        <div class="story-media">\n'
                f'          <iframe\n'
                f'            src="https://www.youtube.com/embed/{yt_id}"\n'
                f'            title="{escape(title)}"\n'
                f'            allow="accelerometer; autoplay; clipboard-write; '
                f'encrypted-media; gyroscope; picture-in-picture"\n'
                f'            allowfullscreen\n'
                f'            loading="lazy">\n'
                f'          </iframe>\n'
                f'        </div>\n'
            )
        elif media_url:
            media_html = (
                f'        <div class="story-media">\n'
                f'          <img src="{escape(media_url)}" '
                f'alt="{escape(title)}" loading="lazy">\n'
                f'        </div>\n'
            )

        # Body parts.
        org_html = (
            f'          <div class="story-org">{escape(org)}</div>\n' if org else ""
        )
        excerpt_html = (
            f'          <p class="story-excerpt">{escape(body)}</p>\n' if body else ""
        )
        link_html = (
            f'          <a class="story-read" href="{escape(link_url)}" '
            f'target="_blank" rel="noopener">Read More &rarr;</a>\n'
            if link_url
            else ""
        )
        date_attr = f' data-date="{escape(date)}"' if date else ""

        # Category label for the badge — capitalized, with "News" rather
        # than "news" for display. Filter pill JS reads data-category, so
        # the visible label can be whatever looks best.
        badge_label = {
            "news": "News",
            "story": "Story",
            "campaign": "Campaign",
        }[category]

        cards.append(
            f'      <article class="story-card {reveal}" '
            f'data-category="{category}"{date_attr}>\n'
            f'        <span class="story-badge story-badge-{category}">'
            f'{badge_label}</span>\n'
            f'{media_html}'
            f'        <div class="story-body">\n'
            f'{org_html}'
            f'          <div class="story-title">{escape(title)}</div>\n'
            f'{excerpt_html}'
            f'{link_html}'
            f'        </div>\n'
            f'      </article>'
        )

    return "\n".join(cards)


def render_map_data(rows: list[dict]) -> str:
    """Render map locations as a JSON blob assigned to a const.

    The map JS in index.html expects each location to have:
      tags      array of strings — drives marker color / legend matching
      img       optional image URL — fills the drawer media slot
      youtube   optional YouTube URL — opens video modal instead of drawer
      project   optional subtitle line below the org name
      urlLabel  optional custom button label (defaults to "Learn More")

    The Sheet's `tags` column is comma-separated (e.g., "clt" or "clt,coop").
    Empty tags default to ["clt"] (white pin / indigo border) so a marker
    always renders, even if the row was added without a tag.

    Header keys are lowercased by fetch_tab(), so the Sheet's "urlLabel"
    column is read as "urllabel" here.
    """
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

        # Comma-separated tags → array. Default ["clt"] keeps markers visible
        # even if a row was added without a tag value.
        tags_raw = (r.get("tags", "") or "").strip()
        tags = [t.strip().lower() for t in tags_raw.split(",") if t.strip()]
        if not tags:
            tags = ["clt"]

        locations.append({
            "id":       r.get("id", "").strip(),
            "tags":     tags,
            "name":     r.get("name", "").strip(),
            "project":  r.get("project", "").strip(),
            "city":     r.get("city", "").strip(),
            "region":   r.get("region", "").strip(),
            "lat":      lat,
            "lng":      lng,
            "desc":     r.get("desc", "").strip(),
            "youtube":  r.get("youtube", "").strip(),
            "img":      r.get("img", "").strip(),
            "url":      r.get("url", "").strip(),
            "urlLabel": r.get("urllabel", "").strip(),
        })

    return "    const LOCATIONS = " + json.dumps(locations, indent=2) + ";"


# ---------------------------------------------------------------------------
# Marker replacement (in-place; idempotent; whitespace-tolerant)
# ---------------------------------------------------------------------------

def _marker_pattern(name: str) -> re.Pattern:
    """Match <!-- BUILD:name:START --> ... <!-- BUILD:name:END --> across lines."""
    return re.compile(
        r"(<!--\s*BUILD:" + re.escape(name) + r":START\s*-->)"
        r"(.*?)"
        r"(<!--\s*BUILD:" + re.escape(name) + r":END\s*-->)",
        re.DOTALL,
    )


def replace_marker(html: str, name: str, new_inner: str) -> tuple[str, bool]:
    """Replace content between BUILD:<name>:START / END markers.

    Also handles the same pattern wrapped in a JS/CSS block comment style:
    /* BUILD:name:START */ ... /* BUILD:name:END */ — used for the map_data
    block which lives inside a <script> tag.

    Returns (new_html, changed). If the marker isn't found, prints a warning
    and returns (html, False) — we never inject markers.
    """
    pattern = _marker_pattern(name)
    match = pattern.search(html)
    if not match:
        # Fall back to JS-comment style markers.
        js_pattern = re.compile(
            r"(/\*\s*BUILD:" + re.escape(name) + r":START\s*\*/)"
            r"(.*?)"
            r"(/\*\s*BUILD:" + re.escape(name) + r":END\s*\*/)",
            re.DOTALL,
        )
        match = js_pattern.search(html)
        if not match:
            print(f"  WARNING: marker BUILD:{name} not found")
            return html, False

    current_inner = match.group(2)
    wrapped = f"\n{new_inner.rstrip()}\n      "
    if current_inner == wrapped:
        return html, False

    new_html = (
        html[: match.start()]
        + match.group(1)
        + wrapped
        + match.group(3)
        + html[match.end():]
    )
    return new_html, True


def inject_page_copy(html: str, copy: dict[str, str]) -> tuple[str, bool]:
    """Replace {{page_copy.key}} placeholders. Returns (new_html, changed)."""
    changed = [False]

    def sub(match: re.Match) -> str:
        key = match.group(1).strip()
        replacement = escape(copy.get(key, f"[missing: {key}]"))
        if match.group(0) != replacement:
            changed[0] = True
        return replacement

    pattern = re.compile(r"\{\{\s*page_copy\.([a-z_0-9]+)\s*\}\}")
    new_html = pattern.sub(sub, html)
    return new_html, changed[0]


# ---------------------------------------------------------------------------
# Per-file orchestration
# ---------------------------------------------------------------------------

def update_file(
    path: Path,
    sections: dict[str, str],
    copy: dict[str, str],
) -> bool:
    """Read file, apply section replacements + page_copy, write back if changed.

    Returns True if the file was actually written.
    """
    if not path.exists():
        print(f"  SKIP: {path.name} does not exist")
        return False

    original = path.read_text(encoding="utf-8")
    html = original

    file_changed = False
    for name, new_inner in sections.items():
        html, changed = replace_marker(html, name, new_inner)
        if changed:
            print(f"  {path.name} :: BUILD:{name} updated")
            file_changed = True

    html, copy_changed = inject_page_copy(html, copy)
    if copy_changed:
        print(f"  {path.name} :: page_copy placeholders updated")
        file_changed = True

    if file_changed:
        path.write_text(html, encoding="utf-8")
        return True
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print(f"[{datetime.now(timezone.utc).isoformat()}] build.py start")

    # 1. Fetch page_copy first. This is the only request we make if the
    #    build is gated off — keeps idle cycles cheap.
    print("Fetching page_copy (control + content)...")
    page_copy_rows = fetch_tab("page_copy")
    control, content_copy = split_page_copy(page_copy_rows)

    publish = (control.get("publish") or "").strip().lower()
    token = (control.get("publish_token") or "").strip()
    note = (control.get("note") or "").strip()

    print(f"  publish        = {publish!r}")
    print(f"  publish_token  = {token!r}")
    if note:
        print(f"  note           = {note!r}")

    if publish != "yes":
        print("publish != 'yes' — exiting without changes.")
        return 0

    state = load_state()
    last_token = (state.get("publish_token") or "").strip()

    if token and token == last_token:
        print(f"publish_token unchanged ({token!r}) — exiting without changes.")
        return 0

    if not token:
        print("WARNING: publish_token is empty — proceeding anyway, "
              "but bump a value next time.")

    # 2. Fetch the rest of the content tabs.
    print("Fetching remaining tabs...")
    data = {"page_copy": page_copy_rows}
    for name, tab in CONTENT_TABS.items():
        if name == "page_copy":
            continue
        data[name] = fetch_tab(tab)

    # 3. Render sections.
    print("Rendering sections...")
    sections_for_index = {
        "stories": render_stories(data["stories"]),
        "resources": render_resources(data["resources"]),
        "partners": render_partners(data["partners"]),
        "map_data": render_map_data(data["map_locations"]),
    }
    sections_for_map = {
        "map_data": render_map_data(data["map_locations"]),
    }

    # 4. Update files in place.
    print("Updating index.html...")
    index_changed = update_file(INDEX_PATH, sections_for_index, content_copy)
    print("Updating map.html...")
    map_changed = update_file(MAP_PATH, sections_for_map, content_copy)

    if not (index_changed or map_changed):
        print("No section or copy changes detected — nothing to commit.")
    else:
        print(f"Files changed: "
              f"index={'yes' if index_changed else 'no'}, "
              f"map={'yes' if map_changed else 'no'}")

    # 5. Persist state — even when no file changed, record the token so
    #    we don't redo this work on every cron tick.
    state["publish_token"] = token
    state["last_build_utc"] = datetime.now(timezone.utc).isoformat()
    state["last_note"] = note
    save_state(state)

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
