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
    )
    return output_file


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

    themes_to_run = list(load_all_themes().keys()) if data.get("all_themes") else [data.get("theme", "terracotta")]
    results = []

    if len(themes_to_run) > 1:
        # Resolve coords and fetch OSM data once, then render all themes in parallel
        from lat_lon_parser import parse as parse_coord
        city = data["city"]
        country = data["country"]
        if data.get("latitude") and data.get("longitude"):
            coords = (parse_coord(data["latitude"]), parse_coord(data["longitude"]))
        else:
            coords = cmp.get_coordinates(city, country)

        dist = int(data.get("distance", 18000))
        width = float(data.get("width", 12))
        height = float(data.get("height", 16))
        compensated_dist = dist * (max(height, width) / min(height, width)) / 4
        prefetched = cmp.fetch_map_data(coords, compensated_dist)

        workers = min(4, len(themes_to_run))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(do_generate, {**data, "theme": t}, coords, prefetched): t
                for t in themes_to_run
            }
            for future in as_completed(futures):
                theme_key = futures[future]
                try:
                    output_file = future.result()
                    results.append({"theme": theme_key, "file": os.path.basename(output_file)})
                except Exception as e:
                    results.append({"theme": theme_key, "error": str(e)})
    else:
        try:
            output_file = do_generate({**data, "theme": themes_to_run[0]})
            results.append({"theme": themes_to_run[0], "file": os.path.basename(output_file)})
        except Exception as e:
            results.append({"theme": themes_to_run[0], "error": str(e)})

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
  width:320px;min-width:320px;background:var(--surface);
  border-right:1px solid var(--border);padding:20px 18px;
  overflow-y:auto;display:flex;flex-direction:column;gap:18px;
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
.theme-preview{margin-top:6px;border-radius:var(--r);overflow:hidden;
               border:1px solid var(--border);transition:all .25s}
.theme-hint{font-size:.7rem;color:var(--muted);margin-top:4px;min-height:1.1em;line-height:1.35}

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
.res-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);
          margin-bottom:18px;overflow:hidden;animation:fadeIn .4s ease both}
.res-card img{width:100%;max-height:70vh;object-fit:contain;display:block;background:var(--bg);cursor:pointer}
.res-card .res-foot{padding:11px 14px;display:flex;justify-content:space-between;
                    align-items:center;border-top:1px solid var(--border)}
.res-card .res-title{font-weight:600;font-size:.9rem}

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

/* Responsive */
@media(max-width:760px){
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

  <!-- Location (required) -->
  <div style="display:flex;flex-direction:column;gap:10px">
    <div class="field">
      <label for="city">City *</label>
      <input id="city" type="text" placeholder="e.g. Paris">
    </div>
    <div class="field">
      <label for="country">Country *</label>
      <input id="country" type="text" placeholder="e.g. France">
    </div>
  </div>

  <!-- Radius -->
  <div class="field">
    <label for="distance">Map Radius</label>
    <div class="slider-row">
      <input id="distance" type="range" min="2000" max="30000" step="1000" value="18000">
      <span class="slider-val" id="dist-val">18 km</span>
    </div>
    <div class="hint">4-6 km: small/dense &nbsp;·&nbsp; 8-12 km: medium &nbsp;·&nbsp; 15-20 km: large metro</div>
  </div>

  <!-- Theme -->
  <div class="field">
    <label>Theme</label>
    <div class="theme-grid" id="theme-grid">
      {% for key, t in themes.items() %}
      <div class="chip{% if key == 'terracotta' %} active{% endif %}"
           data-key="{{ key }}" title="{{ t.get('description','') }}">
        <span class="dot" style="background:{{ t.bg }};border-color:{{ t.get('road_primary','#888') }}"></span>
        <span>{{ t.get('name', key) }}</span>
      </div>
      {% endfor %}
    </div>
    <div class="theme-hint" id="theme-hint">{{ themes.get('terracotta',{}).get('description','') }}</div>
    <div class="theme-preview" id="theme-preview"></div>
  </div>

  <!-- Advanced options (collapsed by default) -->
  <details class="adv">
    <summary>Advanced options</summary>
    <div class="adv-body">
      <!-- Dimensions -->
      <div class="row">
        <div class="field"><label for="width">Width (in)</label>
          <input id="width" type="number" value="12" min="4" max="20" step="0.5"></div>
        <div class="field"><label for="height">Height (in)</label>
          <input id="height" type="number" value="16" min="4" max="20" step="0.5"></div>
      </div>
      <div class="field"><label for="format">Output Format</label>
        <select id="format">
          <option value="png" selected>PNG (300 DPI)</option>
          <option value="svg">SVG (vector)</option>
          <option value="pdf">PDF (print)</option>
        </select>
      </div>
      <!-- Custom coordinates -->
      <div class="row">
        <div class="field"><label for="latitude">Latitude</label>
          <input id="latitude" type="text" placeholder="e.g. 48.8566"></div>
        <div class="field"><label for="longitude">Longitude</label>
          <input id="longitude" type="text" placeholder="e.g. 2.3522"></div>
      </div>
      <div class="hint">Leave blank to auto-detect coordinates from city name.</div>
      <!-- Display names (i18n) -->
      <div class="field"><label for="display_city">Custom city label on poster</label>
        <input id="display_city" type="text" placeholder="e.g. 東京 for Tokyo"></div>
      <div class="field"><label for="display_country">Custom country label on poster</label>
        <input id="display_country" type="text" placeholder="e.g. 日本 for Japan"></div>
      <div class="field"><label for="font_family">Google Font</label>
        <input id="font_family" type="text" placeholder="e.g. Noto Sans JP">
        <div class="hint">Leave blank to use the default Roboto font.</div>
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
  if (t) document.getElementById('theme-preview').innerHTML = buildPreview(t);
}

document.querySelectorAll('.chip').forEach(chip =>
  chip.addEventListener('click', () => selectTheme(chip.dataset.key))
);
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

/* Generate */
async function generate(allThemes) {
  const city = document.getElementById('city').value.trim();
  const country = document.getElementById('country').value.trim();
  if (!city || !country) { toast('City and Country are required.', 'err'); return; }

  const payload = {
    city, country,
    latitude: document.getElementById('latitude').value.trim(),
    longitude: document.getElementById('longitude').value.trim(),
    distance: distSlider.value,
    theme: getTheme(),
    display_city: document.getElementById('display_city').value.trim(),
    display_country: document.getElementById('display_country').value.trim(),
    country_label: '',
    font_family: document.getElementById('font_family').value.trim(),
    width: document.getElementById('width').value,
    height: document.getElementById('height').value,
    format: document.getElementById('format').value,
    all_themes: allThemes,
  };

  const btnGen = document.getElementById('btn-gen');
  const btnAll = document.getElementById('btn-all');
  btnGen.disabled = btnAll.disabled = true;
  btnGen.innerHTML = '<span class="spin"></span> Generating…';

  const progress = document.getElementById('progress');
  const fill = document.getElementById('prog-fill');
  const progText = document.getElementById('prog-text');

  progress.style.display = 'block';
  fill.style.width = '15%';
  fill.classList.add('pulse');
  progText.textContent = 'Fetching map data… (30–60 s for a new city)';
  document.getElementById('result').innerHTML = '';
  progress.scrollIntoView({behavior:'smooth', block:'center'});

  try {
    const resp = await fetch('/api/generate', {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload)
    });
    fill.classList.remove('pulse');
    fill.style.width = '100%';

    if (!resp.ok) {
      const msg = (await resp.json()).error || 'Unknown error';
      progText.textContent = 'Error: ' + msg;
      toast(msg, 'err');
      return;
    }

    const data = await resp.json();
    const ok = data.results.filter(r => !r.error).length;
    const fail = data.results.filter(r => r.error).length;
    progText.textContent = ok + ' poster' + (ok !== 1 ? 's' : '') + ' generated' +
                           (fail ? ', ' + fail + ' failed' : '') + '.';
    if (ok) toast(ok + ' poster' + (ok !== 1 ? 's' : '') + ' generated!', 'ok');
    if (fail) toast(fail + ' generation' + (fail !== 1 ? 's' : '') + ' failed.', 'err');

    let html = '';
    data.results.forEach((r, i) => {
      if (r.error) {
        html += '<div class="res-card" style="animation-delay:'+i*0.08+'s">' +
                '<div class="res-foot" style="color:var(--err)">✗ ' + r.theme + ': ' + r.error + '</div></div>';
      } else {
        const ext = payload.format;
        html += '<div class="res-card" style="animation-delay:'+i*0.08+'s">';
        if (ext === 'png') {
          html += '<img src="/posters/'+r.file+'?t='+Date.now()+'" ' +
                  'onclick="openLightbox(\'/posters/'+r.file+'\')">';
        } else {
          html += '<div style="padding:36px;text-align:center;color:var(--muted)">'+
                  ext.toUpperCase()+' generated — download below.</div>';
        }
        html += '<div class="res-foot"><span class="res-title">'+city+', '+country+
                ' — '+(T[r.theme]?.name||r.theme)+'</span>' +
                '<a href="/posters/'+r.file+'" download class="btn btn-sm btn-p">Download '+
                ext.toUpperCase()+'</a></div></div>';
      }
    });
    document.getElementById('result').innerHTML = html;
    setTimeout(() => document.getElementById('result').scrollIntoView({behavior:'smooth'}), 150);

  } catch(e) {
    fill.classList.remove('pulse');
    progText.textContent = 'Network error: ' + e.message;
    toast('Network error: ' + e.message, 'err');
  } finally {
    btnGen.disabled = btnAll.disabled = false;
    btnGen.innerHTML = 'Generate Poster';
  }
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
    app.run(host="127.0.0.1", port=5000, debug=False)
