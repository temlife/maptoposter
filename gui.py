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

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import threading
import uuid
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

from flask import Flask, render_template_string, request, jsonify, send_from_directory  # noqa: E402

import create_map_poster as cmp  # noqa: E402
from font_management import load_fonts  # noqa: E402

app = Flask(__name__)

POSTERS_DIR = Path("posters")
THEMES_DIR = Path("themes")

# In-memory task store: {task_id: {status, message, progress, results}}
tasks: dict = {}


def load_all_themes():
    """Load all theme JSON files and return metadata dict."""
    result = {}
    for f in sorted(THEMES_DIR.glob("*.json")):
        with open(f, "r", encoding="utf-8") as fh:
            result[f.stem] = json.load(fh)
    return result


def do_generate(params, coords=None, prefetched=None):
    """
    Call the core create_poster function. Returns the output file path.

    Args:
        params: Request parameters dict
        coords: Optional pre-resolved (lat, lon) tuple to skip geocoding
        prefetched: Optional (graph, water, parks) tuple from fetch_map_data()
                    to skip OSM downloads when rendering multiple themes
    """
    from lat_lon_parser import parse as parse_coord

    city = params["city"]
    country = params["country"]

    if coords is None:
        if params.get("latitude") and params.get("longitude"):
            coords = (parse_coord(params["latitude"]), parse_coord(params["longitude"]))
        else:
            coords = cmp.get_coordinates(city, country)

    custom_fonts = None
    if params.get("font_family"):
        custom_fonts = load_fonts(params["font_family"])

    theme = cmp.load_theme(params["theme"])
    layers = params.get("layers") or None  # None → all layers
    dpi = int(params.get("dpi", 300))

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
        theme=theme,
        prefetched=prefetched,
        layers=layers,
        dpi=dpi,
        layout=params.get("layout", "bottom"),
        tagline=params.get("tagline") or None,
        separator=params.get("separator", "line"),
        vignette=params.get("vignette", True),
        grain=params.get("grain", False),
        show_country=params.get("show_country", True),
        show_coords=params.get("show_coords", True),
    )
    return output_file


# ---------------------------------------------------------------------------
# Background task runner
# ---------------------------------------------------------------------------

def _run_task(task_id: str, data: dict) -> None:
    """Run poster generation in a background thread, updating task progress."""
    task = tasks[task_id]

    def upd(**kw: object) -> None:
        task.update(kw)

    try:
        upd(status="running", message="Resolving coordinates…", progress=0.05)

        from lat_lon_parser import parse as parse_coord
        city = data["city"]
        country = data["country"]

        if data.get("latitude") and data.get("longitude"):
            coords = (parse_coord(data["latitude"]), parse_coord(data["longitude"]))
        else:
            coords = cmp.get_coordinates(city, country)

        themes_to_run = (
            list(load_all_themes().keys()) if data.get("all_themes")
            else [data.get("theme", "terracotta")]
        )
        n = len(themes_to_run)

        upd(message="Fetching map data…", progress=0.15)

        if n > 1:
            dist = int(data.get("distance", 18000))
            width = float(data.get("width", 12))
            height = float(data.get("height", 16))
            comp_dist = dist * (max(height, width) / min(height, width)) / 4
            prefetched = cmp.fetch_map_data(coords, comp_dist, layers=data.get("layers") or None)
        else:
            prefetched = None

        results: list = []

        if n > 1:
            workers = min(4, n)
            upd(message=f"Rendering {n} themes in parallel…", progress=0.35)
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(do_generate, {**data, "theme": tk}, coords, prefetched): tk
                    for tk in themes_to_run
                }
                done = 0
                for future in as_completed(futures):
                    tk = futures[future]
                    done += 1
                    upd(progress=0.35 + 0.6 * done / n, message=f"Rendered {done}/{n} themes…")
                    try:
                        out = future.result()
                        results.append({"theme": tk, "file": os.path.basename(out)})
                    except Exception as exc:
                        results.append({"theme": tk, "error": str(exc)})
        else:
            upd(message="Rendering poster…", progress=0.35)
            try:
                out = do_generate({**data, "theme": themes_to_run[0]}, coords)
                results.append({"theme": themes_to_run[0], "file": os.path.basename(out)})
                upd(progress=0.95)
            except Exception as exc:
                results.append({"theme": themes_to_run[0], "error": str(exc)})

        ok = sum(1 for r in results if "error" not in r)
        summary = f"{ok} poster{'s' if ok != 1 else ''} generated"
        if ok < n:
            summary += f", {n - ok} failed"
        upd(status="done", progress=1.0, message=summary, results=results)

    except Exception as exc:
        task.update({"status": "error", "message": str(exc), "progress": 1.0})


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    themes = load_all_themes()
    return render_template_string(HTML_TEMPLATE, themes=themes)


@app.route("/api/generate", methods=["POST"])
def api_generate():
    data = request.get_json()
    if not data or not data.get("city") or not data.get("country"):
        return jsonify({"error": "City and Country are required."}), 400

    task_id = uuid.uuid4().hex[:8]
    tasks[task_id] = {"status": "pending", "message": "Starting…", "progress": 0.0, "results": None}
    threading.Thread(target=_run_task, args=(task_id, data), daemon=True).start()
    return jsonify({"task_id": task_id})


@app.route("/api/task/<task_id>")
def get_task(task_id: str):
    task = tasks.get(task_id)
    if task is None:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(task)


@app.route("/api/geocode")
def api_geocode():
    city = request.args.get("city", "").strip()
    country = request.args.get("country", "").strip()
    if not city or not country:
        return jsonify({"error": "city and country required"}), 400
    try:
        lat, lon = cmp.get_coordinates(city, country)
        return jsonify({"lat": lat, "lon": lon})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 404


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
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#f7f5f2;--surface:#fff;--border:#e2ded8;
  --text:#2c2420;--muted:#8a7e74;
  --accent:#a0522d;--accent-h:#8b4513;
  --r:8px;--ok:#2e7d32;--err:#c62828;
}
[data-theme=dark]{
  --bg:#1a1a1e;--surface:#242428;--border:#3a3a40;
  --text:#e4e2de;--muted:#908d88;
  --accent:#d4956b;--accent-h:#e0a87e;
  --ok:#4caf50;--err:#ef5350;
}
html{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
     font-size:15px;color:var(--text);background:var(--bg)}
body{display:flex;min-height:100vh}

/* Sidebar */
.sidebar{
  width:400px;min-width:400px;background:var(--surface);
  border-right:1px solid var(--border);padding:22px 20px;
  overflow-y:auto;display:flex;flex-direction:column;gap:20px;
}
.section-label{
  font-size:.62rem;font-weight:700;text-transform:uppercase;
  letter-spacing:.1em;color:var(--muted);padding-bottom:8px;
  border-bottom:1px solid var(--border);
}
.app-title{font-size:1.35rem;font-weight:700;letter-spacing:.04em;color:var(--accent)}
.app-sub{font-size:.75rem;color:var(--muted);margin-top:2px}

/* Fields */
.field{display:flex;flex-direction:column;gap:4px}
.field label{font-size:.8rem;font-weight:600}
input[type=text],input[type=number],select{
  width:100%;padding:8px 10px;border:1px solid var(--border);border-radius:var(--r);
  font-size:.88rem;background:var(--bg);color:var(--text);outline:none;transition:border .15s;
}
input:focus,select:focus{border-color:var(--accent)}
.row{display:flex;gap:10px}.row>.field{flex:1}
.hint{font-size:.7rem;color:var(--muted);line-height:1.4}

/* Slider */
.slider-row{display:flex;align-items:center;gap:10px;margin-top:2px}
.slider-row input[type=range]{flex:1;accent-color:var(--accent)}
.slider-val{font-size:.82rem;font-weight:700;color:var(--accent);min-width:44px;text-align:right}

/* Theme grid */
.theme-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:6px;margin-top:4px}
.chip{
  display:flex;align-items:center;gap:7px;padding:7px 10px;
  border:2px solid var(--border);border-radius:var(--r);cursor:pointer;
  font-size:.8rem;transition:all .15s;background:var(--surface);
}
.chip:hover{border-color:var(--accent)}
.chip.active{border-color:var(--accent);background:color-mix(in srgb,var(--accent) 8%,transparent)}
.dot{width:13px;height:13px;border-radius:50%;flex-shrink:0;border:1.5px solid rgba(0,0,0,.15)}
.theme-hint{font-size:.7rem;color:var(--muted);margin-top:4px;line-height:1.35}
.chip{position:relative}
.chip-tip{
  position:absolute;left:50%;bottom:calc(100% + 8px);transform:translateX(-50%);
  width:80px;background:var(--surface);border:1px solid var(--border);
  border-radius:var(--r);overflow:hidden;box-shadow:0 4px 14px rgba(0,0,0,.18);
  opacity:0;pointer-events:none;transition:opacity .15s;z-index:100;
}
.chip:hover .chip-tip{opacity:1}

/* Advanced */
details.adv{border:1px solid var(--border);border-radius:var(--r);overflow:hidden}
details.adv summary{
  padding:8px 12px;cursor:pointer;font-size:.8rem;font-weight:600;
  background:var(--bg);user-select:none;list-style:none;
  display:flex;align-items:center;gap:6px;
}
details.adv summary::before{content:"▸";font-size:.7rem;transition:transform .2s;flex-shrink:0}
details.adv[open] summary::before{transform:rotate(90deg)}
details.adv[open] summary{border-bottom:1px solid var(--border)}
details.adv .adv-body{padding:12px;display:flex;flex-direction:column;gap:10px}

/* Buttons */
.btn{
  display:inline-flex;align-items:center;justify-content:center;gap:7px;
  padding:10px 16px;border:none;border-radius:var(--r);font-size:.9rem;
  font-weight:600;cursor:pointer;transition:all .2s;width:100%;text-decoration:none;
}
.btn-p{background:var(--accent);color:#fff}.btn-p:hover{background:var(--accent-h)}
.btn-s{background:var(--bg);color:var(--text);border:1px solid var(--border)}
.btn-s:hover{border-color:var(--accent);color:var(--accent)}
.btn:disabled{opacity:.45;cursor:not-allowed}
.btn-sm{padding:5px 11px;font-size:.78rem;width:auto}

/* Main */
.main{flex:1;padding:28px 36px;overflow-y:auto}
.main h2{font-size:1.4rem;margin-bottom:8px}
.main .lead{color:var(--muted);margin-bottom:24px;line-height:1.5}


/* Results */
#result{margin-top:20px}
.res-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px}
.res-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);
          overflow:hidden;animation:fadeIn .4s ease both;display:flex;flex-direction:column}
.res-card img{width:100%;aspect-ratio:3/4;object-fit:cover;object-position:center;
              display:block;background:var(--bg);cursor:pointer}
.res-card .res-foot{padding:9px 12px;display:flex;justify-content:space-between;
                    align-items:center;border-top:1px solid var(--border);gap:8px;margin-top:auto}
.res-card .res-title{font-weight:600;font-size:.8rem;overflow:hidden;
                     text-overflow:ellipsis;white-space:nowrap;min-width:0}
/* Single result: wider card, full image */
.res-grid.single .res-card img{aspect-ratio:unset;max-height:70vh;object-fit:contain}

/* Progress */
#progress{display:none;margin-top:16px}
.prog-bar{height:5px;background:var(--border);border-radius:3px;overflow:hidden}
.prog-fill{height:100%;background:var(--accent);transition:width .4s;width:0%}
.prog-fill.pulse{animation:pulse 1.4s ease-in-out infinite}
#prog-text{font-size:.78rem;color:var(--muted);margin-top:6px;text-align:center}

/* Spinner */
@keyframes spin{to{transform:rotate(360deg)}}
.spin{width:16px;height:16px;border:2px solid var(--border);border-top-color:var(--accent);
      border-radius:50%;animation:spin .6s linear infinite;display:inline-block}

/* Animations */
@keyframes fadeIn{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.45}}
@keyframes toastIn{from{transform:translateX(110%)}to{transform:translateX(0)}}
@keyframes toastOut{from{transform:translateX(0)}to{transform:translateX(110%)}}

/* Dark toggle */
.dark-btn{background:none;border:1px solid var(--border);border-radius:var(--r);
          cursor:pointer;padding:5px 8px;font-size:1rem;color:var(--text);
          transition:border-color .2s;flex-shrink:0}
.dark-btn:hover{border-color:var(--accent)}

/* Toasts */
.toasts{position:fixed;bottom:20px;right:20px;z-index:9999;
        display:flex;flex-direction:column-reverse;gap:6px;pointer-events:none}
.toast{pointer-events:auto;padding:10px 16px;border-radius:var(--r);color:#fff;
       font-size:.85rem;font-weight:500;min-width:220px;max-width:360px;
       box-shadow:0 4px 14px rgba(0,0,0,.2);animation:toastIn .3s ease both;
       display:flex;align-items:center;gap:8px}
.toast.out{animation:toastOut .3s ease both}
.toast.ok{background:var(--ok)}.toast.err{background:var(--err)}.toast.info{background:var(--accent)}

/* Lightbox */
.lightbox{position:fixed;inset:0;z-index:9000;background:rgba(0,0,0,.85);
          display:none;align-items:center;justify-content:center;cursor:zoom-out}
.lightbox.open{display:flex}
.lightbox img{max-width:92vw;max-height:92vh;object-fit:contain;border-radius:4px}
.lb-close{position:absolute;top:18px;right:24px;color:#fff;font-size:2rem;
          cursor:pointer;background:none;border:none;opacity:.7}
.lb-close:hover{opacity:1}
.lb-dl{position:absolute;bottom:20px;right:24px}

/* Map picker */
#coord-map{height:200px;border-radius:var(--r);border:1px solid var(--border);margin-top:4px;z-index:0}

/* Pill chips (layers, separator) */
.layer-row{display:flex;flex-wrap:wrap;gap:5px;margin-top:4px}
.layer-chip{display:flex;align-items:center;gap:5px;padding:5px 11px;
  border:1.5px solid var(--border);border-radius:20px;font-size:.8rem;
  cursor:pointer;transition:border-color .15s,background .15s;user-select:none;position:relative}
.layer-chip input{display:none}
.layer-chip:has(input:checked){border-color:var(--accent);background:color-mix(in srgb,var(--accent) 8%,transparent)}
.layer-chip:hover{border-color:var(--accent)}

/* Chip tooltip via data-tip attribute */
.layer-chip[data-tip]:hover::after{
  content:attr(data-tip);position:absolute;bottom:calc(100% + 7px);left:50%;
  transform:translateX(-50%);background:var(--text);color:var(--bg);
  font-size:.68rem;padding:4px 9px;border-radius:5px;white-space:nowrap;
  z-index:200;pointer-events:none;font-weight:400;font-style:normal}
.layer-chip[data-tip]:hover::before{
  content:'';position:absolute;bottom:calc(100% + 3px);left:50%;
  transform:translateX(-50%);border:4px solid transparent;
  border-top-color:var(--text);z-index:200}

/* Layout pills with SVG diagram */
.layout-row{display:flex;gap:8px;margin-top:4px}
.layout-pill{flex:1;display:flex;flex-direction:column;align-items:center;
  justify-content:center;gap:6px;padding:10px 6px;
  border:1.5px solid var(--border);border-radius:var(--r);font-size:.75rem;
  cursor:pointer;transition:all .15s;user-select:none;color:var(--text)}
.layout-pill input{display:none}
.layout-pill:has(input:checked){border-color:var(--accent);background:color-mix(in srgb,var(--accent) 8%,transparent)}
.layout-pill:hover{border-color:var(--accent)}

/* Sub-details (accordion within sections) */
details.sub{border-top:1px solid var(--border);padding-top:10px}
details.sub summary{
  font-size:.8rem;font-weight:600;cursor:pointer;list-style:none;
  display:flex;align-items:center;gap:6px;color:var(--muted);
  user-select:none;padding-bottom:8px}
details.sub summary::before{content:"▸";font-size:.65rem;transition:transform .2s;flex-shrink:0}
details.sub[open] summary::before{transform:rotate(90deg)}
details.sub[open] summary{color:var(--text)}
details.sub .sub-body{display:flex;flex-direction:column;gap:12px;padding-bottom:4px}

/* Effects chips (full-width with description) */
.fx-chip{display:flex;align-items:center;gap:12px;padding:10px 14px;
  border:1.5px solid var(--border);border-radius:var(--r);
  cursor:pointer;transition:all .15s;user-select:none}
.fx-chip input{
  width:16px;height:16px;accent-color:var(--accent);
  flex-shrink:0;cursor:pointer}
.fx-chip:has(input:checked){border-color:var(--accent);background:color-mix(in srgb,var(--accent) 8%,transparent)}
.fx-chip:hover{border-color:var(--accent)}
.fx-title{font-size:.82rem;font-weight:600}
.fx-desc{font-size:.72rem;color:var(--muted);margin-top:2px}

/* Format presets */
.fmt-chip{display:inline-flex;flex-direction:column;align-items:center;
  padding:6px 12px;border:1.5px solid var(--border);border-radius:var(--r);
  font-size:.8rem;font-weight:600;cursor:pointer;transition:all .15s;user-select:none}
.fmt-chip.active{border-color:var(--accent);background:color-mix(in srgb,var(--accent) 8%,transparent)}
.fmt-chip:hover{border-color:var(--accent)}
.fmt-sub{font-size:.65rem;font-weight:400;color:var(--muted);margin-top:1px}
.fmt-row{display:flex;gap:6px;margin-top:4px;flex-wrap:wrap}

/* Sep preview glyph */
.sep-preview{font-size:.85rem;opacity:.7}

/* Responsive */
@media(max-width:900px){
  body{flex-direction:column}
  .sidebar{width:100%;min-width:0;border-right:none;border-bottom:1px solid var(--border)}
  .main{padding:18px}
  .theme-grid{grid-template-columns:repeat(3,1fr)}
}
</style>
</head>
<body>

<aside class="sidebar">

  <!-- Header -->
  <div style="display:flex;justify-content:space-between;align-items:start">
    <div>
      <div class="app-title">MapToPoster</div>
      <div class="app-sub">Generate minimalist city map posters</div>
    </div>
    <button class="dark-btn" id="dark-btn" onclick="toggleDark()" title="Toggle dark mode">
      <span id="dark-icon">&#9789;</span>
    </button>
  </div>

  <!-- ── LOCATION ── -->
  <div>
    <div class="section-label">Location</div>
    <div style="display:flex;flex-direction:column;gap:10px;margin-top:8px">
      <div class="row">
        <div class="field">
          <label for="city">City *</label>
          <input id="city" type="text" placeholder="e.g. Paris">
        </div>
        <div class="field">
          <label for="country">Country *</label>
          <input id="country" type="text" placeholder="e.g. France">
        </div>
      </div>
      <!-- Map centre toggle (replaces former "Advanced" map picker) -->
      <details class="sub" id="coords-details">
        <summary>Map centre point</summary>
        <div class="sub-body">
          <div id="coord-map"></div>
          <div class="row" style="margin-top:6px">
            <div class="field"><label for="latitude">Latitude</label>
              <input id="latitude" type="text" placeholder="auto-detect"></div>
            <div class="field"><label for="longitude">Longitude</label>
              <input id="longitude" type="text" placeholder="auto-detect"></div>
          </div>
          <div style="display:flex;justify-content:space-between;align-items:center">
            <div class="hint">Click the map to pin the centre, or leave blank to geocode from the city name.</div>
            <button type="button" onclick="clearCoords()"
                    style="font-size:.7rem;color:var(--muted);background:none;border:none;cursor:pointer;text-decoration:underline;padding:0;flex-shrink:0;margin-left:8px">
              Clear
            </button>
          </div>
        </div>
      </details>
    </div>
  </div>

  <!-- ── MAP ── -->
  <div>
    <div class="section-label">Map</div>
    <div style="display:flex;flex-direction:column;gap:12px;margin-top:8px">
      <div class="field">
        <label for="distance">Radius — how much of the city to show</label>
        <div class="slider-row">
          <input id="distance" type="range" min="2000" max="30000" step="1000" value="18000">
          <span class="slider-val" id="dist-val">18 km</span>
        </div>
        <div class="hint">4–6 km: dense neighbourhood &nbsp;·&nbsp; 8–12 km: city centre &nbsp;·&nbsp; 15–20 km: metro area</div>
      </div>
      <div class="field">
        <label>Content — which map layers to render</label>
        <div class="layer-row">
          <label class="layer-chip" data-tip="Rivers, lakes &amp; sea"><input type="checkbox" value="water" checked> Water</label>
          <label class="layer-chip" data-tip="Parks &amp; green spaces"><input type="checkbox" value="parks" checked> Parks</label>
          <label class="layer-chip" data-tip="Residential, retail &amp; industrial zones"><input type="checkbox" value="landuse"> Land use</label>
          <label class="layer-chip" data-tip="Building footprints — slow on large radii"><input type="checkbox" value="buildings"> Buildings</label>
          <label class="layer-chip" data-tip="Metro, tram &amp; rail lines"><input type="checkbox" value="railways" checked> Railways</label>
        </div>
      </div>
    </div>
  </div>

  <!-- ── STYLE ── -->
  <div>
    <div class="section-label">Style</div>
    <div style="display:flex;flex-direction:column;gap:0;margin-top:8px">

      <!-- Theme -->
      <div class="field" style="margin-bottom:14px">
        <label>Theme — colour palette for the poster</label>
        <div class="theme-grid" id="theme-grid" style="margin-top:6px">
          {% for key, t in themes.items() %}
          <div class="chip{% if key == 'terracotta' %} active{% endif %}"
               data-key="{{ key }}" title="{{ t.get('description','') }}">
            <span class="dot" style="background:{{ t.bg }};border-color:{{ t.get('road_primary','#888') }}"></span>
            <span>{{ t.get('name', key) }}</span>
          </div>
          {% endfor %}
        </div>
        <div class="theme-hint" id="theme-hint" style="margin-top:5px">{{ themes.get('terracotta',{}).get('description','') }}</div>
      </div>

      <!-- Visual effects (inline — no sub-accordion) -->
      <div class="field" style="margin-bottom:14px">
        <label>Visual effects</label>
        <div style="display:flex;flex-direction:column;gap:6px;margin-top:6px">
          <label class="fx-chip">
            <input type="checkbox" id="fx-vignette" checked>
            <div>
              <div class="fx-title">Vignette</div>
              <div class="fx-desc">Darkens the edges for a polished print look</div>
            </div>
          </label>
          <label class="fx-chip">
            <input type="checkbox" id="fx-grain">
            <div>
              <div class="fx-title">Grain</div>
              <div class="fx-desc">Adds a subtle paper texture — handcrafted feel</div>
            </div>
          </label>
        </div>
      </div>

      <!-- Sub: Text & layout -->
      <details class="sub">
        <summary>Text &amp; layout</summary>
        <div class="sub-body">
          <div class="field">
            <label for="tagline">Tagline <span style="font-weight:400;color:var(--muted)">(optional)</span></label>
            <input id="tagline" type="text" placeholder='e.g. "The City of Lights"'
                   style="font-size:.85rem;font-style:italic">
            <div class="hint">A short subtitle printed below the country name on the poster.</div>
          </div>
          <label class="fx-chip">
            <input type="checkbox" id="show-country" checked onchange="toggleSeparator()">
            <div>
              <div class="fx-title">Show country name</div>
              <div class="fx-desc">Uncheck to remove the country label from the poster</div>
            </div>
          </label>
          <label class="fx-chip">
            <input type="checkbox" id="show-coords" checked>
            <div>
              <div class="fx-title">Show coordinates</div>
              <div class="fx-desc">Uncheck to hide the latitude/longitude line</div>
            </div>
          </label>
          <div class="field">
            <label>Text position</label>
            <div class="layout-row">
              <label class="layout-pill">
                <input type="radio" name="layout" value="bottom" checked>
                <svg viewBox="0 0 20 28" width="30" height="42">
                  <rect x=".5" y=".5" width="19" height="27" rx="1.2" fill="none" stroke="currentColor" stroke-width=".9"/>
                  <line x1="2" y1="5" x2="11" y2="6.5" stroke="currentColor" stroke-width=".7" opacity=".4"/>
                  <line x1="5" y1="8" x2="18" y2="7" stroke="currentColor" stroke-width=".6" opacity=".4"/>
                  <line x1="2" y1="11" x2="13" y2="10" stroke="currentColor" stroke-width=".5" opacity=".3"/>
                  <line x1="9" y1="13.5" x2="18" y2="14.5" stroke="currentColor" stroke-width=".5" opacity=".3"/>
                  <rect x=".5" y="15.5" width="19" height="5" fill="currentColor" opacity=".06"/>
                  <rect x="4" y="19" width="12" height="1.6" rx=".4" fill="currentColor" opacity=".65"/>
                  <rect x="6.5" y="22" width="7" height="1" rx=".3" fill="currentColor" opacity=".45"/>
                  <rect x="7.5" y="24.5" width="5" height=".8" rx=".3" fill="currentColor" opacity=".3"/>
                </svg>
                Bottom
              </label>
              <label class="layout-pill">
                <input type="radio" name="layout" value="top">
                <svg viewBox="0 0 20 28" width="30" height="42">
                  <rect x=".5" y=".5" width="19" height="27" rx="1.2" fill="none" stroke="currentColor" stroke-width=".9"/>
                  <rect x="4" y="3" width="12" height="1.6" rx=".4" fill="currentColor" opacity=".65"/>
                  <rect x="6.5" y="5.5" width="7" height="1" rx=".3" fill="currentColor" opacity=".45"/>
                  <rect x="7.5" y="8" width="5" height=".8" rx=".3" fill="currentColor" opacity=".3"/>
                  <rect x=".5" y="8.5" width="19" height="5" fill="currentColor" opacity=".06"/>
                  <line x1="2" y1="16" x2="11" y2="17.5" stroke="currentColor" stroke-width=".7" opacity=".4"/>
                  <line x1="5" y1="19" x2="18" y2="18" stroke="currentColor" stroke-width=".6" opacity=".4"/>
                  <line x1="2" y1="22" x2="13" y2="21" stroke="currentColor" stroke-width=".5" opacity=".3"/>
                  <line x1="9" y1="24.5" x2="18" y2="25.5" stroke="currentColor" stroke-width=".5" opacity=".3"/>
                </svg>
                Top
              </label>
            </div>
          </div>
          <!-- Separator: hidden when "Show country" is unchecked -->
          <div class="field" id="separator-field">
            <label>Separator — decorative line between city and country</label>
            <div class="layer-row" style="margin-top:4px">
              <label class="layer-chip"><input type="radio" name="separator" value="line" checked>
                <span class="sep-preview">———</span> Line</label>
              <label class="layer-chip"><input type="radio" name="separator" value="double">
                <span class="sep-preview" style="font-size:.7rem;line-height:1.2;letter-spacing:1px">══</span> Double</label>
              <label class="layer-chip"><input type="radio" name="separator" value="dots">
                <span class="sep-preview" style="letter-spacing:3px">···</span> Dots</label>
            </div>
          </div>
        </div>
      </details>

    </div>
  </div>

  <!-- ── EXPORT ── (always visible) -->
  <div>
    <div class="section-label">Export</div>
    <div style="display:flex;flex-direction:column;gap:10px;margin-top:8px">
      <div class="field">
        <label>Paper format</label>
        <div class="fmt-row" style="margin-top:4px">
          <span class="fmt-chip" data-fmt="A4" onclick="setFormatPreset('A4',8.27,11.69)">
            A4<span class="fmt-sub">21×30 cm</span>
          </span>
          <span class="fmt-chip" data-fmt="A3" onclick="setFormatPreset('A3',11.69,16.54)">
            A3<span class="fmt-sub">30×42 cm</span>
          </span>
          <span class="fmt-chip" data-fmt="A2" onclick="setFormatPreset('A2',16.54,23.39)">
            A2<span class="fmt-sub">42×59 cm</span>
          </span>
          <span class="fmt-chip active" data-fmt="custom" onclick="setFormatPreset('custom',0,0)">
            Custom
          </span>
        </div>
      </div>
      <div id="custom-dims" class="row">
        <div class="field"><label for="width">Width (inches)</label>
          <input id="width" type="number" value="12" min="4" max="20" step="0.5"></div>
        <div class="field"><label for="height">Height (inches)</label>
          <input id="height" type="number" value="16" min="4" max="20" step="0.5"></div>
      </div>
      <div class="row">
        <div class="field"><label for="format">File format</label>
          <select id="format">
            <option value="png" selected>PNG — standard image</option>
            <option value="svg">SVG — scalable vector</option>
            <option value="pdf">PDF — ready to print</option>
          </select>
        </div>
        <div class="field"><label for="dpi">Resolution</label>
          <select id="dpi">
            <option value="150">Fast preview (150 dpi)</option>
            <option value="300" selected>Print quality (300 dpi)</option>
          </select>
        </div>
      </div>
    </div>
  </div>

  <!-- ── CUSTOMIZATION (collapsed) ── -->
  <details class="adv">
    <summary>Customization</summary>
    <div class="adv-body">
      <div class="field">
        <label for="display_city">Custom city label <span style="font-weight:400;color:var(--muted)">(for non-Latin scripts)</span></label>
        <input id="display_city" type="text" placeholder="e.g. 東京 for Tokyo">
        <div class="hint">Overrides the city text printed on the poster.</div>
      </div>
      <div class="field">
        <label for="display_country">Custom country label</label>
        <input id="display_country" type="text" placeholder="e.g. 日本 for Japan">
      </div>
      <div class="field">
        <label for="font_family">Google Font</label>
        <input id="font_family" type="text" placeholder="e.g. Noto Sans JP">
        <div class="hint">Font family name from Google Fonts. Leave blank for the default Roboto.</div>
      </div>
    </div>
  </details>

  <!-- Actions -->
  <div style="margin-top:auto;display:flex;flex-direction:column;gap:8px;padding-top:4px">
    <button class="btn btn-p" id="btn-gen" onclick="generate(false)">Generate Poster</button>
    <button class="btn btn-s" id="btn-all" onclick="generate(true)">Generate All Themes</button>
    <div class="hint" style="text-align:center">Ctrl+Enter to generate</div>
  </div>

</aside>

<main class="main">
  <div id="welcome">
    <h2>Welcome</h2>
    <p class="lead">Enter a city and country, choose a theme, then click <strong>Generate Poster</strong>.<br>
    Your poster will appear here — it may take 30–60 s the first time while map data is downloaded.</p>
  </div>

  <div id="progress">
    <div class="prog-bar"><div class="prog-fill" id="prog-fill"></div></div>
    <div id="prog-text">Generating…</div>
  </div>
  <div id="result"></div>
</main>

<!-- Lightbox -->
<div class="lightbox" id="lightbox" onclick="closeLightbox()">
  <button class="lb-close" onclick="closeLightbox()">&times;</button>
  <img id="lb-img" src="" alt="Poster preview">
  <a class="lb-dl btn btn-sm btn-p" id="lb-dl" href="" download onclick="event.stopPropagation()">Download</a>
</div>

<div class="toasts" id="toasts"></div>

<script>
const T = {{ themes | tojson }};

/* Toast */
function toast(msg, type='info', ms=4000) {
  const icons = {ok:'✓', err:'✗', info:'ℹ'};
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.textContent = (icons[type]||'') + ' ' + msg;
  document.getElementById('toasts').appendChild(el);
  setTimeout(() => {
    el.classList.add('out');
    el.addEventListener('animationend', () => el.remove());
  }, ms);
}

/* Format presets */
function setFormatPreset(name, w, h) {
  document.querySelectorAll('.fmt-chip').forEach(c => c.classList.toggle('active', c.dataset.fmt === name));
  const customDims = document.getElementById('custom-dims');
  if (name === 'custom') {
    customDims.style.display = '';
  } else {
    document.getElementById('width').value = w;
    document.getElementById('height').value = h;
    customDims.style.display = 'none';
  }
}

/* Radius slider */
const distSlider = document.getElementById('distance');
distSlider.addEventListener('input', () => {
  document.getElementById('dist-val').textContent = Math.round(distSlider.value / 1000) + ' km';
});

/* Theme chips */
function buildPreview(t) {
  return '<svg viewBox="0 0 120 160" xmlns="http://www.w3.org/2000/svg">' +
    '<rect width="120" height="160" fill="'+t.bg+'"/>' +
    '<ellipse cx="95" cy="55" rx="40" ry="28" fill="'+t.water+'" opacity=".7"/>' +
    '<ellipse cx="30" cy="70" rx="18" ry="14" fill="'+t.parks+'" opacity=".7"/>' +
    '<line x1="0" y1="40" x2="120" y2="50" stroke="'+(t.road_motorway||'#888')+'" stroke-width="2.5" opacity=".9"/>' +
    '<line x1="60" y1="0" x2="55" y2="160" stroke="'+(t.road_primary||'#999')+'" stroke-width="1.8" opacity=".8"/>' +
    '<line x1="0" y1="90" x2="120" y2="85" stroke="'+(t.road_secondary||'#aaa')+'" stroke-width="1.2" opacity=".7"/>' +
    '<line x1="25" y1="0" x2="30" y2="160" stroke="'+(t.road_tertiary||'#bbb')+'" stroke-width=".8" opacity=".6"/>' +
    '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">' +
    '<stop offset="0" stop-color="'+t.bg+'" stop-opacity="0"/>' +
    '<stop offset="1" stop-color="'+t.bg+'" stop-opacity="1"/></linearGradient></defs>' +
    '<rect x="0" y="110" width="120" height="50" fill="url(#g)"/>' +
    '<text x="60" y="140" text-anchor="middle" fill="'+t.text+'" font-size="9" font-weight="700" ' +
    'font-family="sans-serif" letter-spacing="3">CITY</text>' +
    '<line x1="45" y1="144" x2="75" y2="144" stroke="'+t.text+'" stroke-width=".5" opacity=".6"/>' +
    '<text x="60" y="152" text-anchor="middle" fill="'+t.text+'" font-size="5" ' +
    'font-family="sans-serif" opacity=".7">COUNTRY</text></svg>';
}

function selectTheme(key) {
  document.querySelectorAll('.chip').forEach(c => c.classList.toggle('active', c.dataset.key === key));
  const t = T[key];
  document.getElementById('theme-hint').textContent = (t && t.description) || '';
}

document.querySelectorAll('.chip').forEach(chip => {
  chip.addEventListener('click', () => selectTheme(chip.dataset.key));
  // Inject hover preview tooltip
  const t = T[chip.dataset.key];
  if (t) {
    const tip = document.createElement('div');
    tip.className = 'chip-tip';
    tip.innerHTML = buildPreview(t);
    chip.appendChild(tip);
  }
});
selectTheme('terracotta');

function getTheme() {
  return (document.querySelector('.chip.active') || {}).dataset?.key || 'terracotta';
}

/* Lightbox */
function openLightbox(src) {
  document.getElementById('lb-img').src = src;
  document.getElementById('lb-dl').href = src;
  document.getElementById('lightbox').classList.add('open');
  document.body.style.overflow = 'hidden';
}
function closeLightbox() {
  document.getElementById('lightbox').classList.remove('open');
  document.body.style.overflow = '';
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeLightbox(); });

/* Generate — non-blocking: starts a background task, then polls for status */
async function generate(allThemes) {
  const city = document.getElementById('city').value.trim();
  const country = document.getElementById('country').value.trim();
  if (!city || !country) { toast('City and Country are required.', 'err'); return; }

  const payload = {
    city, country,
    tagline: document.getElementById('tagline').value.trim(),
    latitude: document.getElementById('latitude').value.trim(),
    longitude: document.getElementById('longitude').value.trim(),
    distance: distSlider.value,
    theme: getTheme(),
    layers: Array.from(document.querySelectorAll('.layer-chip input:checked')).map(i => i.value),
    display_city: document.getElementById('display_city').value.trim(),
    display_country: document.getElementById('display_country').value.trim(),
    country_label: '',
    font_family: document.getElementById('font_family').value.trim(),
    width: document.getElementById('width').value,
    height: document.getElementById('height').value,
    format: document.getElementById('format').value,
    dpi: parseInt(document.getElementById('dpi').value),
    layout: document.querySelector('input[name="layout"]:checked')?.value || 'bottom',
    separator: document.querySelector('input[name="separator"]:checked')?.value || 'line',
    vignette: document.getElementById('fx-vignette').checked,
    grain: document.getElementById('fx-grain').checked,
    show_country: document.getElementById('show-country').checked,
    show_coords:  document.getElementById('show-coords').checked,
    all_themes: allThemes,
  };

  const btnGen = document.getElementById('btn-gen');
  const btnAll = document.getElementById('btn-all');
  btnGen.disabled = btnAll.disabled = true;
  btnGen.innerHTML = '<span class="spin"></span> Generating…';

  const fill = document.getElementById('prog-fill');
  const progText = document.getElementById('prog-text');
  const progress = document.getElementById('progress');
  progress.style.display = 'block';
  fill.style.width = '5%';
  progText.textContent = 'Starting…';
  document.getElementById('result').innerHTML = '';
  progress.scrollIntoView({behavior:'smooth', block:'center'});

  try {
    const resp = await fetch('/api/generate', {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload)
    });
    if (!resp.ok) {
      const msg = (await resp.json()).error || 'Server error';
      progText.textContent = 'Error: ' + msg;
      toast(msg, 'err');
      return;
    }
    const {task_id} = await resp.json();
    await pollTask(task_id, city, country, payload.format);
  } catch(e) {
    progText.textContent = 'Network error: ' + e.message;
    toast('Network error: ' + e.message, 'err');
  } finally {
    btnGen.disabled = btnAll.disabled = false;
    btnGen.innerHTML = 'Generate Poster';
  }
}

async function pollTask(taskId, city, country, fmt) {
  const fill = document.getElementById('prog-fill');
  const progText = document.getElementById('prog-text');

  while (true) {
    await new Promise(r => setTimeout(r, 1500));
    let task;
    try { task = await fetch('/api/task/' + taskId).then(r => r.json()); }
    catch { continue; }

    fill.style.width = Math.round((task.progress || 0) * 100) + '%';
    progText.textContent = task.message || '…';

    if (task.status === 'done') {
      const results = task.results || [];
      const ok = results.filter(r => !r.error).length;
      const fail = results.filter(r => r.error).length;
      if (ok) toast(ok + ' poster' + (ok !== 1 ? 's' : '') + ' generated!', 'ok');
      if (fail) toast(fail + ' generation' + (fail !== 1 ? 's' : '') + ' failed.', 'err');

      const ts = Date.now();
      const isSingle = results.length === 1;
      let html = '<div class="res-grid' + (isSingle ? ' single' : '') + '">';
      results.forEach((r, i) => {
        if (r.error) {
          html += '<div class="res-card" style="animation-delay:'+i*0.08+'s">' +
                  '<div class="res-foot" style="color:var(--err)">✗ ' + r.theme + ': ' + r.error + '</div></div>';
        } else {
          html += '<div class="res-card" style="animation-delay:'+i*0.08+'s">';
          if (fmt === 'png') {
            html += '<img src="/posters/'+r.file+'?t='+ts+'" onclick="openLightbox(\'/posters/'+r.file+'\')">';
          } else {
            html += '<div style="padding:36px;text-align:center;color:var(--muted)">'+
                    fmt.toUpperCase()+' generated — download below.</div>';
          }
          html += '<div class="res-foot"><span class="res-title">'+(T[r.theme]?.name||r.theme)+'</span>' +
                  '<a href="/posters/'+r.file+'" download class="btn btn-sm btn-p">↓ '+
                  fmt.toUpperCase()+'</a></div></div>';
        }
      });
      html += '</div>';
      document.getElementById('result').innerHTML = html;
      setTimeout(() => document.getElementById('result').scrollIntoView({behavior:'smooth'}), 150);
      break;
    } else if (task.status === 'error') {
      toast(task.message, 'err');
      break;
    }
  }
}

/* Separator visibility tied to "Show country" checkbox */
function toggleSeparator() {
  const show = document.getElementById('show-country').checked;
  document.getElementById('separator-field').style.display = show ? '' : 'none';
}

/* Leaflet map picker */
let _map = null, _marker = null;
let _geocodedCenter = null; // last city/country geocode result

document.getElementById('coords-details').addEventListener('toggle', function() {
  if (this.open && !_map) {
    const initView = _geocodedCenter || [20, 0];
    const initZoom = _geocodedCenter ? 11 : 2;
    _map = L.map('coord-map').setView(initView, initZoom);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '© <a href="https://openstreetmap.org">OpenStreetMap</a>',
      maxZoom: 19
    }).addTo(_map);
    _map.on('click', e => setCoords(e.latlng.lat, e.latlng.lng));

    // Restore marker if inputs already have values
    const lat = document.getElementById('latitude').value;
    const lng = document.getElementById('longitude').value;
    if (lat && lng) setCoords(parseFloat(lat), parseFloat(lng), false);
  } else if (this.open && _map && _geocodedCenter && !_marker) {
    // Map already exists but city changed — re-centre without placing a marker
    _map.setView(_geocodedCenter, 11);
  }
});

/* Auto-geocode city/country and centre the map */
let _geocodeTimer = null;
function scheduleGeocode() {
  clearTimeout(_geocodeTimer);
  _geocodeTimer = setTimeout(async () => {
    const city    = document.getElementById('city').value.trim();
    const country = document.getElementById('country').value.trim();
    if (city.length < 2 || country.length < 2) return;
    try {
      const res = await fetch('/api/geocode?' + new URLSearchParams({city, country}));
      if (!res.ok) return;
      const {lat, lon} = await res.json();
      if (lat == null || lon == null) return;
      _geocodedCenter = [lat, lon];
      // If the map is already open and no custom pin has been placed, re-centre it
      if (_map && !_marker) _map.setView(_geocodedCenter, 11);
    } catch { /* silently ignore network errors */ }
  }, 600);
}
document.getElementById('city').addEventListener('input', scheduleGeocode);
document.getElementById('country').addEventListener('input', scheduleGeocode);

function setCoords(lat, lng, updateInputs=true) {
  if (updateInputs) {
    document.getElementById('latitude').value = lat.toFixed(5);
    document.getElementById('longitude').value = lng.toFixed(5);
  }
  const pos = L.latLng(lat, lng);
  if (_marker) _marker.setLatLng(pos);
  else _marker = L.marker(pos).addTo(_map);
  _map.setView(pos, Math.max(_map.getZoom(), 10));
}

function clearCoords() {
  document.getElementById('latitude').value = '';
  document.getElementById('longitude').value = '';
  if (_marker && _map) { _map.removeLayer(_marker); _marker = null; }
  if (_map) _map.setView([20, 0], 2);
}

/* Ctrl+Enter */
document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); generate(false); }
});

/* Dark mode */
function applyDark(dark) {
  document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
  document.getElementById('dark-icon').innerHTML = dark ? '&#9788;' : '&#9789;';
}
function toggleDark() {
  const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
  applyDark(!isDark);
  localStorage.setItem('mtp-dark', isDark ? '0' : '1');
}
(function(){
  const s = localStorage.getItem('mtp-dark');
  if (s === '1' || (s === null && matchMedia('(prefers-color-scheme:dark)').matches)) applyDark(true);
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    POSTERS_DIR.mkdir(exist_ok=True)
    print("MapToPoster GUI running at http://localhost:5000")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
