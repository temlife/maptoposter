#!/usr/bin/env python3
"""
Flask GUI for MapToPoster - City Map Poster Generator

Self-contained web interface with all HTML/CSS/JS embedded.
No CDN, no external dependencies - everything runs locally.

Usage:
    python gui.py
    # or
    bash run_gui.sh
"""

import json
import os
import threading
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

from flask import Flask, render_template_string, request, jsonify, send_from_directory  # noqa: E402

import create_map_poster as cmp  # noqa: E402
from font_management import load_fonts  # noqa: E402

app = Flask(__name__)

POSTERS_DIR = Path("posters")
THEMES_DIR = Path("themes")


def load_all_themes():
    """Load all theme JSON files and return metadata dict."""
    result = {}
    for f in sorted(THEMES_DIR.glob("*.json")):
        with open(f, "r", encoding="utf-8") as fh:
            result[f.stem] = json.load(fh)
    return result


def do_generate(params):
    """Call the core create_poster function. Returns the output file path."""
    from lat_lon_parser import parse as parse_coord

    city = params["city"]
    country = params["country"]

    if params.get("latitude") and params.get("longitude"):
        coords = (parse_coord(params["latitude"]), parse_coord(params["longitude"]))
    else:
        coords = cmp.get_coordinates(city, country)

    custom_fonts = None
    if params.get("font_family"):
        custom_fonts = load_fonts(params["font_family"])

    cmp.THEME = cmp.load_theme(params["theme"])

    fmt = params.get("format", "png")
    output_file = cmp.generate_output_filename(city, params["theme"], fmt)
    cmp.create_poster(
        city=city,
        country=country,
        point=coords,
        dist=int(params.get("distance", 18000)),
        output_file=output_file,
        output_format=fmt,
        width=float(params.get("width", 12)),
        height=float(params.get("height", 16)),
        country_label=params.get("country_label") or None,
        display_city=params.get("display_city") or None,
        display_country=params.get("display_country") or None,
        fonts=custom_fonts,
    )
    return output_file


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    themes = load_all_themes()
    existing = sorted(POSTERS_DIR.glob("*.png"), reverse=True)[:6] if POSTERS_DIR.exists() else []
    return render_template_string(
        HTML_TEMPLATE,
        themes=themes,
        existing_posters=[p.name for p in existing],
    )


@app.route("/api/generate", methods=["POST"])
def api_generate():
    data = request.get_json()
    if not data or not data.get("city") or not data.get("country"):
        return jsonify({"error": "City and Country are required."}), 400

    themes_to_run = list(load_all_themes().keys()) if data.get("all_themes") else [data.get("theme", "terracotta")]
    results = []

    for theme_key in themes_to_run:
        try:
            params = {**data, "theme": theme_key}
            output_file = do_generate(params)
            results.append({"theme": theme_key, "file": os.path.basename(output_file)})
        except Exception as e:
            results.append({"theme": theme_key, "error": str(e)})

    return jsonify({"results": results})


@app.route("/posters/<path:filename>")
def serve_poster(filename):
    return send_from_directory(POSTERS_DIR, filename)


# ---------------------------------------------------------------------------
# Embedded HTML / CSS / JS
# ---------------------------------------------------------------------------

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MapToPoster</title>
<style>
/* ── Reset & Variables ─────────────────────────────────────────── */
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#f7f5f2; --surface:#fff; --border:#e2ded8;
  --text:#2c2420; --text-muted:#8a7e74;
  --accent:#a0522d; --accent-hover:#8b4513;
  --sidebar-w:340px; --radius:8px;
  --toast-success:#2e7d32; --toast-error:#c62828;
}
[data-theme="dark"]{
  --bg:#1a1a1e; --surface:#242428; --border:#3a3a40;
  --text:#e4e2de; --text-muted:#908d88;
  --accent:#d4956b; --accent-hover:#e0a87e;
  --toast-success:#4caf50; --toast-error:#ef5350;
}
html{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
     font-size:15px;color:var(--text);background:var(--bg);scroll-behavior:smooth}
body{display:flex;min-height:100vh}

/* ── Sidebar ───────────────────────────────────────────────────── */
.sidebar{
  width:var(--sidebar-w);min-width:var(--sidebar-w);
  background:var(--surface);border-right:1px solid var(--border);
  padding:24px 20px;overflow-y:auto;display:flex;flex-direction:column;gap:20px;
}
.sidebar h1{font-size:1.4rem;letter-spacing:.05em;color:var(--accent)}
.sidebar .caption{font-size:.8rem;color:var(--text-muted)}
.sidebar h2{font-size:.85rem;text-transform:uppercase;letter-spacing:.08em;
             color:var(--text-muted);margin-top:4px}

/* ── Form Controls ─────────────────────────────────────────────── */
label{font-size:.82rem;font-weight:600;display:block;margin-bottom:4px}
input[type="text"],input[type="number"],select{
  width:100%;padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius);
  font-size:.9rem;background:var(--bg);color:var(--text);outline:none;
  transition:border .2s;
}
input:focus,select:focus{border-color:var(--accent)}
.row{display:flex;gap:10px}
.row>*{flex:1}
.range-wrap{display:flex;align-items:center;gap:10px}
.range-wrap input[type="range"]{flex:1;accent-color:var(--accent)}
.range-wrap .range-val{font-size:.85rem;min-width:55px;text-align:right;color:var(--accent);font-weight:600}
.hint{font-size:.72rem;color:var(--text-muted);margin-top:2px}

/* ── Collapsible ───────────────────────────────────────────────── */
details{border:1px solid var(--border);border-radius:var(--radius);overflow:hidden}
details summary{padding:8px 12px;cursor:pointer;font-size:.82rem;font-weight:600;
                background:var(--bg);user-select:none}
details[open] summary{border-bottom:1px solid var(--border)}
details .inner{padding:12px;display:flex;flex-direction:column;gap:10px}

/* ── Theme Selector ────────────────────────────────────────────── */
.theme-select{display:flex;flex-wrap:wrap;gap:6px;max-height:200px;overflow-y:auto;padding:4px 0}
.theme-chip{
  display:flex;align-items:center;gap:6px;
  padding:5px 10px;border:2px solid var(--border);border-radius:20px;
  cursor:pointer;font-size:.78rem;transition:all .15s;background:var(--surface);
}
.theme-chip:hover{border-color:var(--accent)}
.theme-chip.active{border-color:var(--accent);background:rgba(160,82,45,.08)}
[data-theme="dark"] .theme-chip.active{background:rgba(212,149,107,.12)}
.swatch{width:14px;height:14px;border-radius:50%;border:1px solid rgba(128,128,128,.3)}
.theme-desc{font-size:.72rem;color:var(--text-muted);margin-top:2px;min-height:1.2em}

/* ── Mini Poster Preview (sidebar) ─────────────────────────────── */
.mini-preview{margin-top:8px;border-radius:var(--radius);overflow:hidden;border:1px solid var(--border);
              transition:all .3s ease}

/* ── Buttons ───────────────────────────────────────────────────── */
.btn{
  display:inline-flex;align-items:center;justify-content:center;gap:8px;
  padding:10px 16px;border:none;border-radius:var(--radius);font-size:.9rem;
  font-weight:600;cursor:pointer;transition:all .2s;width:100%;text-decoration:none;
}
.btn-primary{background:var(--accent);color:#fff}
.btn-primary:hover{background:var(--accent-hover)}
.btn-secondary{background:var(--bg);color:var(--text);border:1px solid var(--border)}
.btn-secondary:hover{border-color:var(--accent);color:var(--accent)}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn-sm{padding:6px 12px;font-size:.8rem;width:auto}

/* ── Presets ───────────────────────────────────────────────────── */
.presets{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}
.preset{
  padding:3px 8px;border:1px solid var(--border);border-radius:12px;
  font-size:.7rem;cursor:pointer;background:var(--bg);color:var(--text-muted);
  transition:all .15s;
}
.preset:hover{border-color:var(--accent);color:var(--accent)}
.preset.active{border-color:var(--accent);background:var(--accent);color:#fff}

/* ── Main Content ──────────────────────────────────────────────── */
.main{flex:1;padding:32px 40px;overflow-y:auto}
.main h2{font-size:1.5rem;margin-bottom:8px}
.main p.lead{color:var(--text-muted);margin-bottom:28px}

/* ── Distance Guide ────────────────────────────────────────────── */
.guide{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:32px}
.guide-card{background:var(--surface);border:1px solid var(--border);
            border-radius:var(--radius);padding:16px;text-align:center}
.guide-card .value{font-size:1.3rem;font-weight:700;color:var(--accent)}
.guide-card .label{font-size:.82rem;font-weight:600;margin-bottom:4px}
.guide-card .example{font-size:.75rem;color:var(--text-muted);margin-top:4px}

/* ── Theme Gallery (enriched mini-posters) ─────────────────────── */
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px;margin-bottom:32px}
.gallery-card{
  border:1px solid var(--border);border-radius:var(--radius);
  background:var(--surface);transition:all .2s;overflow:hidden;cursor:default;
}
.gallery-card:hover{transform:translateY(-3px);box-shadow:0 6px 20px rgba(0,0,0,.1)}
.gallery-card svg{display:block;width:100%}
.gallery-card .card-info{padding:10px 12px}
.gallery-card .name{font-weight:600;font-size:.85rem;margin-bottom:2px}
.gallery-card .desc{font-size:.72rem;color:var(--text-muted);line-height:1.3}

/* ── Poster Grid ───────────────────────────────────────────────── */
.poster-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px;margin-top:16px}
.poster-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
             overflow:hidden;transition:all .2s}
.poster-card:hover{transform:translateY(-2px);box-shadow:0 6px 20px rgba(0,0,0,.08)}
.poster-card img{width:100%;display:block;cursor:pointer}
.poster-card .info{padding:10px 12px;font-size:.8rem;color:var(--text-muted);
                   display:flex;justify-content:space-between;align-items:center}

/* ── Result Area ───────────────────────────────────────────────── */
#result{margin-top:24px}
.result-item{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
             margin-bottom:20px;overflow:hidden;
             animation:fadeSlideIn .4s ease both}
.result-item img{width:100%;max-height:70vh;object-fit:contain;display:block;
                 background:var(--bg);cursor:pointer}
.result-item .bar{padding:12px 16px;display:flex;justify-content:space-between;align-items:center;
                  border-top:1px solid var(--border)}
.result-item .bar .title{font-weight:600}

/* ── Progress ──────────────────────────────────────────────────── */
#progress{display:none;margin-top:16px}
.progress-bar{height:6px;background:var(--border);border-radius:3px;overflow:hidden}
.progress-bar .fill{height:100%;background:var(--accent);transition:width .4s ease;width:0%}
.progress-bar .fill.pulse{animation:progressPulse 1.5s ease-in-out infinite}
.progress-text{font-size:.8rem;color:var(--text-muted);margin-top:6px;text-align:center}

/* ── Spinner ───────────────────────────────────────────────────── */
@keyframes spin{to{transform:rotate(360deg)}}
.spinner{width:18px;height:18px;border:2px solid var(--border);border-top-color:var(--accent);
         border-radius:50%;animation:spin .6s linear infinite;display:inline-block}

/* ── Animations ────────────────────────────────────────────────── */
@keyframes fadeSlideIn{
  from{opacity:0;transform:translateY(16px)}
  to{opacity:1;transform:translateY(0)}
}
@keyframes progressPulse{
  0%,100%{opacity:1}
  50%{opacity:.5}
}
@keyframes toastIn{
  from{transform:translateX(120%);opacity:0}
  to{transform:translateX(0);opacity:1}
}
@keyframes toastOut{
  from{transform:translateX(0);opacity:1}
  to{transform:translateX(120%);opacity:0}
}

/* ── Dark-mode toggle ──────────────────────────────────────────── */
.theme-toggle{
  background:none;border:1px solid var(--border);border-radius:var(--radius);
  cursor:pointer;padding:6px 8px;font-size:1.1rem;line-height:1;
  color:var(--text);transition:border-color .2s;flex-shrink:0;
}
.theme-toggle:hover{border-color:var(--accent)}

/* ── Toast Notifications ───────────────────────────────────────── */
.toast-container{position:fixed;bottom:24px;right:24px;z-index:9999;
                 display:flex;flex-direction:column-reverse;gap:8px;pointer-events:none}
.toast{
  pointer-events:auto;
  padding:12px 20px;border-radius:var(--radius);color:#fff;
  font-size:.88rem;font-weight:500;min-width:260px;max-width:400px;
  box-shadow:0 4px 16px rgba(0,0,0,.2);
  animation:toastIn .3s ease both;
  display:flex;align-items:center;gap:10px;
}
.toast.removing{animation:toastOut .3s ease both}
.toast.success{background:var(--toast-success)}
.toast.error{background:var(--toast-error)}
.toast.info{background:var(--accent)}
.toast-icon{font-size:1.1rem;flex-shrink:0}

/* ── Lightbox ──────────────────────────────────────────────────── */
.lightbox{
  position:fixed;inset:0;z-index:9000;background:rgba(0,0,0,.85);
  display:none;align-items:center;justify-content:center;
  cursor:zoom-out;animation:fadeSlideIn .25s ease;
}
.lightbox.open{display:flex}
.lightbox img{max-width:92vw;max-height:92vh;object-fit:contain;border-radius:4px;
              box-shadow:0 0 60px rgba(0,0,0,.5)}
.lightbox-close{position:absolute;top:20px;right:28px;color:#fff;font-size:2rem;
                cursor:pointer;opacity:.7;transition:opacity .2s;background:none;border:none}
.lightbox-close:hover{opacity:1}
.lightbox-dl{position:absolute;bottom:24px;right:28px}

/* ── History ───────────────────────────────────────────────────── */
.history-item{
  display:flex;justify-content:space-between;align-items:center;
  padding:6px 8px;border-radius:6px;cursor:pointer;font-size:.78rem;
  transition:background .15s;gap:6px;
}
.history-item:hover{background:var(--bg)}
.history-item .hi-city{font-weight:600;color:var(--text);white-space:nowrap;
                        overflow:hidden;text-overflow:ellipsis;max-width:140px}
.history-item .hi-meta{color:var(--text-muted);font-size:.7rem;white-space:nowrap}
.history-empty{font-size:.75rem;color:var(--text-muted);padding:6px 8px;font-style:italic}
.history-clear{font-size:.7rem;color:var(--text-muted);cursor:pointer;border:none;
               background:none;text-decoration:underline;padding:0;margin-top:4px}
.history-clear:hover{color:var(--accent)}

/* ── Responsive ────────────────────────────────────────────────── */
@media(max-width:800px){
  body{flex-direction:column}
  .sidebar{width:100%;min-width:0;border-right:none;border-bottom:1px solid var(--border)}
  .main{padding:20px}
  .guide{grid-template-columns:1fr}
  .gallery{grid-template-columns:repeat(auto-fill,minmax(140px,1fr))}
}
</style>
</head>
<body>

<!-- ═══════════════════════════ SIDEBAR ═══════════════════════════ -->
<aside class="sidebar">
  <div style="display:flex;justify-content:space-between;align-items:start">
    <div>
      <h1>MapToPoster</h1>
      <div class="caption">Generate minimalist map posters from OpenStreetMap data</div>
    </div>
    <button class="theme-toggle" id="theme-toggle" onclick="toggleDark()" title="Toggle dark mode">
      <span id="toggle-icon">&#9789;</span>
    </button>
  </div>

  <!-- Location -->
  <div>
    <h2>Location</h2>
    <div style="display:flex;flex-direction:column;gap:10px;margin-top:8px">
      <div><label for="city">City *</label>
           <input type="text" id="city" placeholder="e.g. Paris"></div>
      <div><label for="country">Country *</label>
           <input type="text" id="country" placeholder="e.g. France"></div>
    </div>
  </div>

  <details>
    <summary>Custom Coordinates</summary>
    <div class="inner">
      <div><label for="latitude">Latitude</label>
           <input type="text" id="latitude" placeholder="e.g. 48.8566"></div>
      <div><label for="longitude">Longitude</label>
           <input type="text" id="longitude" placeholder="e.g. 2.3522"></div>
    </div>
  </details>

  <!-- Distance -->
  <div>
    <label for="distance">Map Radius</label>
    <div class="range-wrap">
      <input type="range" id="distance" min="2000" max="30000" step="1000" value="18000">
      <span class="range-val" id="distance-val">18 000 m</span>
    </div>
    <div class="hint">4-6 km: small &bull; 8-12 km: medium &bull; 15-20 km: large</div>
  </div>

  <!-- Theme -->
  <div>
    <h2>Theme</h2>
    <div class="theme-select" id="theme-select">
      {% for key, t in themes.items() %}
      <div class="theme-chip{% if key == 'terracotta' %} active{% endif %}"
           data-theme="{{ key }}" title="{{ t.get('description','') }}">
        <span class="swatch" style="background:{{ t.bg }}"></span>
        <span>{{ t.get('name', key) }}</span>
      </div>
      {% endfor %}
    </div>
    <div class="theme-desc" id="theme-desc">
      {{ themes.get('terracotta',{}).get('description','') }}
    </div>
    <!-- [2] Live mini-poster preview -->
    <div class="mini-preview" id="mini-preview"></div>
  </div>

  <!-- Display Options -->
  <details>
    <summary>Display Options (i18n)</summary>
    <div class="inner">
      <div><label for="display_city">Display City Name</label>
           <input type="text" id="display_city" placeholder="Custom name for poster"></div>
      <div><label for="display_country">Display Country Name</label>
           <input type="text" id="display_country" placeholder="Custom country name"></div>
      <div><label for="country_label">Country Label</label>
           <input type="text" id="country_label" placeholder="Override country text"></div>
      <div><label for="font_family">Google Font Family</label>
           <input type="text" id="font_family" placeholder='e.g. Noto Sans JP'>
           <div class="hint">Leave blank for bundled Roboto</div></div>
    </div>
  </details>

  <!-- Dimensions -->
  <div>
    <h2>Poster Dimensions</h2>
    <!-- [1] Presets -->
    <div class="presets" id="presets">
      <span class="preset" data-w="12" data-h="16" onclick="applyPreset(this)">Poster 3:4</span>
      <span class="preset" data-w="3.6" data-h="3.6" onclick="applyPreset(this)">Instagram</span>
      <span class="preset" data-w="8.3" data-h="11.7" onclick="applyPreset(this)">A4</span>
      <span class="preset" data-w="12.8" data-h="7.2" onclick="applyPreset(this)">4K Wallpaper</span>
      <span class="preset" data-w="3.6" data-h="6.4" onclick="applyPreset(this)">Mobile</span>
    </div>
    <div class="row" style="margin-top:8px">
      <div><label for="width">Width (in)</label>
           <input type="number" id="width" value="12" min="4" max="20" step="0.5"></div>
      <div><label for="height">Height (in)</label>
           <input type="number" id="height" value="16" min="4" max="20" step="0.5"></div>
    </div>
    <div style="margin-top:10px">
      <label for="format">Output Format</label>
      <select id="format">
        <option value="png" selected>PNG (300 DPI)</option>
        <option value="svg">SVG (vector)</option>
        <option value="pdf">PDF (print)</option>
      </select>
    </div>
  </div>

  <!-- [4] History -->
  <details id="history-section">
    <summary>Recent Generations</summary>
    <div class="inner" id="history-list">
      <div class="history-empty">No history yet.</div>
    </div>
  </details>

  <!-- Actions -->
  <div style="margin-top:auto;display:flex;flex-direction:column;gap:8px">
    <button class="btn btn-primary" id="btn-generate" onclick="generate(false)">
      Generate Poster
    </button>
    <button class="btn btn-secondary" id="btn-all" onclick="generate(true)">
      Generate All Themes
    </button>
    <div class="hint" style="text-align:center">Ctrl+Enter to generate</div>
  </div>
</aside>

<!-- ═══════════════════════════ MAIN ══════════════════════════════ -->
<main class="main" id="main-content">
  <h2>Welcome</h2>
  <p class="lead">Configure your poster in the sidebar, then click <strong>Generate Poster</strong>.</p>

  <!-- Distance Guide -->
  <div class="guide">
    <div class="guide-card">
      <div class="label">Small / Dense Cities</div>
      <div class="value">4 000 - 6 000 m</div>
      <div class="example">Venice, Amsterdam center</div>
    </div>
    <div class="guide-card">
      <div class="label">Medium Cities</div>
      <div class="value">8 000 - 12 000 m</div>
      <div class="example">Paris, Barcelona</div>
    </div>
    <div class="guide-card">
      <div class="label">Large Metros</div>
      <div class="value">15 000 - 20 000 m</div>
      <div class="example">Tokyo, Mumbai</div>
    </div>
  </div>

  <!-- [7] Theme Gallery with mini-posters -->
  <h3 style="margin-bottom:12px">Available Themes</h3>
  <div class="gallery">
    {% for key, t in themes.items() %}
    <div class="gallery-card">
      <svg viewBox="0 0 120 160" xmlns="http://www.w3.org/2000/svg">
        <rect width="120" height="160" fill="{{ t.bg }}"/>
        <!-- Water -->
        <ellipse cx="95" cy="55" rx="40" ry="28" fill="{{ t.water }}" opacity=".7"/>
        <!-- Parks -->
        <ellipse cx="30" cy="70" rx="18" ry="14" fill="{{ t.parks }}" opacity=".7"/>
        <ellipse cx="85" cy="110" rx="12" ry="10" fill="{{ t.parks }}" opacity=".5"/>
        <!-- Roads -->
        <line x1="0" y1="40" x2="120" y2="50" stroke="{{ t.get('road_motorway','#888') }}" stroke-width="2.5" opacity=".9"/>
        <line x1="60" y1="0" x2="55" y2="160" stroke="{{ t.get('road_primary','#999') }}" stroke-width="1.8" opacity=".8"/>
        <line x1="0" y1="90" x2="120" y2="85" stroke="{{ t.get('road_secondary','#aaa') }}" stroke-width="1.2" opacity=".7"/>
        <line x1="25" y1="0" x2="30" y2="160" stroke="{{ t.get('road_tertiary','#bbb') }}" stroke-width=".8" opacity=".6"/>
        <line x1="90" y1="0" x2="85" y2="160" stroke="{{ t.get('road_residential','#ccc') }}" stroke-width=".5" opacity=".5"/>
        <line x1="0" y1="130" x2="120" y2="125" stroke="{{ t.get('road_residential','#ccc') }}" stroke-width=".5" opacity=".4"/>
        <!-- Bottom gradient -->
        <defs><linearGradient id="g-{{ key }}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="{{ t.bg }}" stop-opacity="0"/>
          <stop offset="1" stop-color="{{ t.bg }}" stop-opacity="1"/>
        </linearGradient></defs>
        <rect x="0" y="110" width="120" height="50" fill="url(#g-{{ key }})"/>
        <!-- Text -->
        <text x="60" y="140" text-anchor="middle" fill="{{ t.text }}" font-size="9" font-weight="700"
              font-family="-apple-system,sans-serif" letter-spacing="3">CITY</text>
        <line x1="45" y1="144" x2="75" y2="144" stroke="{{ t.text }}" stroke-width=".5" opacity=".6"/>
        <text x="60" y="152" text-anchor="middle" fill="{{ t.text }}" font-size="5"
              font-family="-apple-system,sans-serif" opacity=".7">COUNTRY</text>
      </svg>
      <div class="card-info">
        <div class="name">{{ t.get('name', key) }}</div>
        <div class="desc">{{ t.get('description', '') }}</div>
      </div>
    </div>
    {% endfor %}
  </div>

  <!-- Existing Posters -->
  {% if existing_posters %}
  <h3 style="margin-bottom:12px">Previously Generated</h3>
  <div class="poster-grid">
    {% for name in existing_posters %}
    <div class="poster-card">
      <img src="/posters/{{ name }}" alt="{{ name }}" loading="lazy"
           onclick="openLightbox('/posters/{{ name }}', '{{ name }}')">
      <div class="info">
        <span>{{ name }}</span>
        <a href="/posters/{{ name }}" download class="btn btn-sm btn-secondary">Download</a>
      </div>
    </div>
    {% endfor %}
  </div>
  {% endif %}

  <!-- Progress & Results -->
  <div id="progress">
    <div class="progress-bar"><div class="fill" id="progress-fill"></div></div>
    <div class="progress-text" id="progress-text">Preparing...</div>
  </div>
  <div id="result"></div>
</main>

<!-- [8] Lightbox -->
<div class="lightbox" id="lightbox" onclick="closeLightbox(event)">
  <button class="lightbox-close" onclick="closeLightbox(event)">&times;</button>
  <img id="lightbox-img" src="" alt="Poster preview">
  <a class="lightbox-dl btn btn-sm btn-primary" id="lightbox-dl" href="" download
     onclick="event.stopPropagation()">Download</a>
</div>

<!-- [3] Toast container -->
<div class="toast-container" id="toast-container"></div>

<!-- ═══════════════════════════ JS ════════════════════════════════ -->
<script>
const T = {{ themes | tojson }};

/* ── [3] Toast Notifications ───────────────────────────────────── */
function showToast(msg, type='info', ms=4000) {
  const c = document.getElementById('toast-container');
  const icons = {success:'&#10003;', error:'&#10007;', info:'&#9432;'};
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.innerHTML = '<span class="toast-icon">' + (icons[type]||'') + '</span>' + msg;
  c.appendChild(el);
  setTimeout(() => {
    el.classList.add('removing');
    el.addEventListener('animationend', () => el.remove());
  }, ms);
}

/* ── Distance slider ───────────────────────────────────────────── */
const distSlider = document.getElementById('distance');
const distVal = document.getElementById('distance-val');
distSlider.addEventListener('input', () => {
  distVal.textContent = Number(distSlider.value).toLocaleString('fr-FR') + ' m';
});

/* ── Theme selector + [2] live preview ─────────────────────────── */
function buildMiniPoster(t) {
  return '<svg viewBox="0 0 120 160" xmlns="http://www.w3.org/2000/svg">' +
    '<rect width="120" height="160" fill="'+t.bg+'"/>' +
    '<ellipse cx="95" cy="55" rx="40" ry="28" fill="'+t.water+'" opacity=".7"/>' +
    '<ellipse cx="30" cy="70" rx="18" ry="14" fill="'+t.parks+'" opacity=".7"/>' +
    '<line x1="0" y1="40" x2="120" y2="50" stroke="'+(t.road_motorway||'#888')+'" stroke-width="2.5" opacity=".9"/>' +
    '<line x1="60" y1="0" x2="55" y2="160" stroke="'+(t.road_primary||'#999')+'" stroke-width="1.8" opacity=".8"/>' +
    '<line x1="0" y1="90" x2="120" y2="85" stroke="'+(t.road_secondary||'#aaa')+'" stroke-width="1.2" opacity=".7"/>' +
    '<line x1="25" y1="0" x2="30" y2="160" stroke="'+(t.road_tertiary||'#bbb')+'" stroke-width=".8" opacity=".6"/>' +
    '<line x1="90" y1="0" x2="85" y2="160" stroke="'+(t.road_residential||'#ccc')+'" stroke-width=".5" opacity=".5"/>' +
    '<defs><linearGradient id="mp" x1="0" y1="0" x2="0" y2="1">' +
    '<stop offset="0" stop-color="'+t.bg+'" stop-opacity="0"/>' +
    '<stop offset="1" stop-color="'+t.bg+'" stop-opacity="1"/></linearGradient></defs>' +
    '<rect x="0" y="110" width="120" height="50" fill="url(#mp)"/>' +
    '<text x="60" y="140" text-anchor="middle" fill="'+t.text+'" font-size="9" font-weight="700" '+
    'font-family="-apple-system,sans-serif" letter-spacing="3">CITY</text>' +
    '<line x1="45" y1="144" x2="75" y2="144" stroke="'+t.text+'" stroke-width=".5" opacity=".6"/>' +
    '<text x="60" y="152" text-anchor="middle" fill="'+t.text+'" font-size="5" '+
    'font-family="-apple-system,sans-serif" opacity=".7">COUNTRY</text></svg>';
}

function updatePreview(key) {
  const el = document.getElementById('mini-preview');
  if (T[key]) el.innerHTML = buildMiniPoster(T[key]);
}

document.querySelectorAll('.theme-chip').forEach(chip => {
  chip.addEventListener('click', () => {
    document.querySelectorAll('.theme-chip').forEach(c => c.classList.remove('active'));
    chip.classList.add('active');
    const key = chip.dataset.theme;
    document.getElementById('theme-desc').textContent = (T[key] && T[key].description) || '';
    updatePreview(key);
    updateFavicon(key);
  });
});
// Init preview
updatePreview('terracotta');

function getSelectedTheme() {
  const a = document.querySelector('.theme-chip.active');
  return a ? a.dataset.theme : 'terracotta';
}

/* ── [1] Dimension Presets ─────────────────────────────────────── */
function applyPreset(el) {
  document.getElementById('width').value = el.dataset.w;
  document.getElementById('height').value = el.dataset.h;
  document.querySelectorAll('.preset').forEach(p => p.classList.remove('active'));
  el.classList.add('active');
}

/* ── [4] History ───────────────────────────────────────────────── */
const HISTORY_KEY = 'mtp-history';
function getHistory() {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY)) || []; } catch { return []; }
}
function saveHistory(params) {
  const h = getHistory();
  h.unshift({
    city: params.city, country: params.country, theme: params.theme,
    distance: params.distance, width: params.width, height: params.height,
    format: params.format, ts: Date.now()
  });
  // Keep last 10
  localStorage.setItem(HISTORY_KEY, JSON.stringify(h.slice(0, 10)));
  renderHistory();
}
function renderHistory() {
  const list = document.getElementById('history-list');
  const h = getHistory();
  if (!h.length) { list.innerHTML = '<div class="history-empty">No history yet.</div>'; return; }
  let html = '';
  h.forEach((item, i) => {
    const date = new Date(item.ts);
    const time = date.toLocaleDateString('fr-FR', {day:'numeric',month:'short'}) +
                 ' ' + date.toLocaleTimeString('fr-FR', {hour:'2-digit',minute:'2-digit'});
    html += '<div class="history-item" onclick="loadHistory('+i+')">' +
      '<span class="hi-city">' + item.city + ', ' + item.country + '</span>' +
      '<span class="hi-meta">' + (T[item.theme]?.name || item.theme) + ' &middot; ' + time + '</span></div>';
  });
  html += '<button class="history-clear" onclick="clearHistory()">Clear history</button>';
  list.innerHTML = html;
}
function loadHistory(i) {
  const item = getHistory()[i];
  if (!item) return;
  document.getElementById('city').value = item.city;
  document.getElementById('country').value = item.country;
  document.getElementById('distance').value = item.distance || 18000;
  distVal.textContent = Number(item.distance || 18000).toLocaleString('fr-FR') + ' m';
  document.getElementById('width').value = item.width || 12;
  document.getElementById('height').value = item.height || 16;
  document.getElementById('format').value = item.format || 'png';
  // Select theme chip
  const chip = document.querySelector('.theme-chip[data-theme="'+item.theme+'"]');
  if (chip) chip.click();
  showToast('Settings restored for ' + item.city, 'info', 2000);
}
function clearHistory() {
  localStorage.removeItem(HISTORY_KEY);
  renderHistory();
}
renderHistory();

/* ── [8] Lightbox ──────────────────────────────────────────────── */
function openLightbox(src, filename) {
  const lb = document.getElementById('lightbox');
  document.getElementById('lightbox-img').src = src;
  document.getElementById('lightbox-dl').href = src;
  lb.classList.add('open');
  document.body.style.overflow = 'hidden';
}
function closeLightbox(e) {
  if (e && e.target.tagName === 'IMG') return; // don't close on img click
  document.getElementById('lightbox').classList.remove('open');
  document.body.style.overflow = '';
}
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeLightbox(e);
});

/* ── [9] Dynamic Favicon ───────────────────────────────────────── */
function updateFavicon(themeKey) {
  const t = T[themeKey];
  if (!t) return;
  const svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">' +
    '<rect width="32" height="32" rx="6" fill="'+t.bg+'"/>' +
    '<line x1="4" y1="10" x2="28" y2="12" stroke="'+(t.road_motorway||'#888')+'" stroke-width="2.5"/>' +
    '<line x1="16" y1="2" x2="14" y2="30" stroke="'+(t.road_primary||'#999')+'" stroke-width="1.8"/>' +
    '<circle cx="24" cy="8" r="5" fill="'+t.water+'" opacity=".7"/>' +
    '<circle cx="8" cy="22" r="4" fill="'+t.parks+'" opacity=".7"/>' +
    '<rect x="0" y="24" width="32" height="8" fill="'+t.bg+'"/>' +
    '<text x="16" y="30" text-anchor="middle" fill="'+t.text+'" font-size="6" font-weight="700" '+
    'font-family="sans-serif">M</text></svg>';
  let link = document.querySelector('link[rel="icon"]');
  if (!link) { link = document.createElement('link'); link.rel = 'icon'; document.head.appendChild(link); }
  link.href = 'data:image/svg+xml,' + encodeURIComponent(svg);
}
updateFavicon('terracotta');

/* ── [6] Generate (with animations + [3] toasts + [4] history) ── */
async function generate(allThemes) {
  const city = document.getElementById('city').value.trim();
  const country = document.getElementById('country').value.trim();
  if (!city || !country) {
    showToast('City and Country are required.', 'error');
    return;
  }

  const payload = {
    city, country,
    latitude: document.getElementById('latitude').value.trim(),
    longitude: document.getElementById('longitude').value.trim(),
    distance: distSlider.value,
    theme: getSelectedTheme(),
    display_city: document.getElementById('display_city').value.trim(),
    display_country: document.getElementById('display_country').value.trim(),
    country_label: document.getElementById('country_label').value.trim(),
    font_family: document.getElementById('font_family').value.trim(),
    width: document.getElementById('width').value,
    height: document.getElementById('height').value,
    format: document.getElementById('format').value,
    all_themes: allThemes,
  };

  const btnGen = document.getElementById('btn-generate');
  const btnAll = document.getElementById('btn-all');
  btnGen.disabled = btnAll.disabled = true;
  btnGen.innerHTML = '<span class="spinner"></span> Generating...';

  const progress = document.getElementById('progress');
  const progressFill = document.getElementById('progress-fill');
  const progressText = document.getElementById('progress-text');
  const resultDiv = document.getElementById('result');

  progress.style.display = 'block';
  progressFill.style.width = '20%';
  progressFill.classList.add('pulse');
  progressText.textContent = 'Generating poster... this may take 30-60s for new cities.';
  resultDiv.innerHTML = '';

  // Smooth scroll to progress
  progress.scrollIntoView({behavior:'smooth', block:'center'});

  try {
    const resp = await fetch('/api/generate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });

    progressFill.classList.remove('pulse');
    progressFill.style.width = '100%';

    if (!resp.ok) {
      const err = await resp.json();
      const msg = err.error || 'Unknown error';
      progressText.textContent = 'Error: ' + msg;
      showToast(msg, 'error');
      return;
    }

    const data = await resp.json();
    const successes = data.results.filter(r => !r.error).length;
    const errors = data.results.filter(r => r.error).length;
    progressText.textContent = successes + ' poster(s) generated' + (errors ? ', ' + errors + ' error(s)' : '') + '.';

    if (successes) showToast(successes + ' poster(s) generated!', 'success');
    if (errors) showToast(errors + ' generation(s) failed.', 'error');

    // Save to history
    saveHistory(payload);

    // Render results with staggered animation
    let html = '';
    data.results.forEach((r, i) => {
      if (r.error) {
        html += '<div class="result-item" style="animation-delay:'+i*0.1+'s">' +
                '<div class="bar" style="color:var(--toast-error)">Error (' + r.theme + '): ' + r.error + '</div></div>';
      } else {
        const ext = payload.format;
        const isPng = ext === 'png';
        html += '<div class="result-item" style="animation-delay:'+i*0.1+'s">';
        if (isPng) {
          html += '<img src="/posters/' + r.file + '?t=' + Date.now() + '" alt="' + r.file + '" ' +
                  'onclick="openLightbox(\'/posters/' + r.file + '\',\'' + r.file + '\')">';
        } else {
          html += '<div style="padding:40px;text-align:center;color:var(--text-muted)">' +
                  ext.toUpperCase() + ' generated &mdash; use the download button below.</div>';
        }
        html += '<div class="bar"><span class="title">' + city + ', ' + country +
                ' &mdash; ' + (T[r.theme]?.name || r.theme) + '</span>' +
                '<a href="/posters/' + r.file + '" download class="btn btn-sm btn-primary">' +
                'Download ' + ext.toUpperCase() + '</a></div></div>';
      }
    });
    resultDiv.innerHTML = html;

    // Scroll to first result
    setTimeout(() => {
      resultDiv.scrollIntoView({behavior:'smooth', block:'start'});
    }, 200);

  } catch (e) {
    progressFill.classList.remove('pulse');
    progressText.textContent = 'Network error: ' + e.message;
    showToast('Network error: ' + e.message, 'error');
  } finally {
    btnGen.disabled = btnAll.disabled = false;
    btnGen.innerHTML = 'Generate Poster';
  }
}

/* ── [5] Ctrl+Enter shortcut ───────────────────────────────────── */
document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault();
    generate(false);
  }
});

/* ── Dark mode ─────────────────────────────────────────────────── */
function applyDarkMode(dark) {
  document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
  document.getElementById('toggle-icon').innerHTML = dark ? '&#9788;' : '&#9789;';
}
function toggleDark() {
  const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
  applyDarkMode(!isDark);
  localStorage.setItem('mtp-dark', !isDark ? '1' : '0');
}
(function(){
  const saved = localStorage.getItem('mtp-dark');
  if (saved === '1') applyDarkMode(true);
  else if (saved === null && window.matchMedia('(prefers-color-scheme: dark)').matches) applyDarkMode(true);
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    POSTERS_DIR.mkdir(exist_ok=True)
    print("MapToPoster GUI running at http://localhost:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
