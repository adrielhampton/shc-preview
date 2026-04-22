# SHC Build System

Static site built from a Google Sheet, deployed via GitHub Pages.

## How it works

1. **Google Sheet** (4 tabs: `page_copy`, `map_locations`, `partners`, `resources`) holds all editable content.
2. **GitHub Actions** runs every 10 minutes, fetches each tab as CSV, regenerates `index.html` and `map.html` from the templates in `/templates/`, commits if anything changed.
3. **GitHub Pages** serves the updated files.

End-to-end latency from Sheet edit to live site: ~2–12 minutes.

## Repo layout

```
/
├── .github/workflows/build.yml   # Runs the build
├── scripts/build.py              # Fetches Sheets, renders sections
├── templates/
│   ├── index.html                # Homepage template (has BUILD markers)
│   └── map.html                  # Map template
├── images/                       # All site images (logos, partner marks, location photos)
├── index.html                    # GENERATED — do not edit by hand
└── map.html                      # GENERATED — do not edit by hand
```

## Setup checklist

1. Create the Google Sheet with the four tabs (schemas below).
2. File → Share → Publish to web → Entire Document → CSV → Publish.
3. Copy the sheet ID from the URL (`docs.google.com/spreadsheets/d/SHEET_ID/edit`).
4. In GitHub repo → Settings → Secrets and variables → Actions → New secret:
   - Name: `SHEET_ID`
   - Value: the sheet ID
5. Settings → Pages → Source: `main` branch / root.
6. Actions tab → Run "Build site from Google Sheets" manually to seed the first build.

## Sheet schemas

### `page_copy`
| key | value |

Keys currently used: `hero_headline`, `hero_sub`, `whatis_intro`, `whois_intro`, `resources_intro`, `signup_headline`, `footer_tagline`. Add more by putting a new key in the sheet and referencing `{{page_copy.new_key}}` in a template.

### `map_locations`
| id | type | name | city | region | lat | lng | desc | units | url | active |

`type` values: `clt`, `public`, `coop`.

### `partners`
| name | url | logo_filename | active |

`logo_filename` is the filename in `/images/partners/` (e.g. `acce.svg`). Leave blank to render the name as text.

### `resources`
| title | description | link | category | date | active |

Sorted by `date` descending automatically. `date` format: `YYYY-MM-DD`. Links open in a new tab.

## Editing templates

Two kinds of injection:

**Sections** — wrap a region with BUILD markers:
```html
<div class="resources-grid">
  <!-- BUILD:resources:START -->
  <!-- BUILD:resources:END -->
</div>
```
The script replaces everything between the markers on each build.

**Page copy** — inline `{{page_copy.key}}` anywhere in the template:
```html
<h1>{{page_copy.hero_headline}}</h1>
```

## Manual rebuild

GitHub → Actions tab → "Build site from Google Sheets" → Run workflow. Takes about 30 seconds.
