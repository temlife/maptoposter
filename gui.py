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
}
[data-theme="dark"]{
  --bg:#1a1a1e; --surface:#242428; --border:#3a3a40;
  --text:#e4e2de; --text-muted:#908d88;
  --accent:#d4956b; --accent-hover:#e0a87e;
}
html{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
     font-size:15px;color:var(--text);background:var(--bg)}
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
.swatch{width:14px;height:14px;border-radius:50%;border:1px solid rgba(0,0,0,.15)}
.theme-desc{font-size:.72rem;color:var(--text-muted);margin-top:2px;min-height:1.2em}

/* ── Buttons ───────────────────────────────────────────────────── */
.btn{
  display:inline-flex;align-items:center;justify-content:center;gap:8px;
  padding:10px 16px;border:none;border-radius:var(--radius);font-size:.9rem;
  font-weight:600;cursor:pointer;transition:all .2s;width:100%;
}
.btn-primary{background:var(--accent);color:#fff}
.btn-primary:hover{background:var(--accent-hover)}
.btn-secondary{background:var(--bg);color:var(--text);border:1px solid var(--border)}
.btn-secondary:hover{border-color:var(--accent);color:var(--accent)}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn-sm{padding:6px 12px;font-size:.8rem;width:auto}

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

/* ── Theme Gallery ─────────────────────────────────────────────── */
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;margin-bottom:32px}
.gallery-card{
  border:1px solid var(--border);border-radius:var(--radius);padding:12px;
  background:var(--surface);transition:transform .15s;
}
.gallery-card:hover{transform:translateY(-2px)}
.gallery-card .name{font-weight:600;font-size:.9rem;margin-bottom:4px}
.gallery-card .desc{font-size:.75rem;color:var(--text-muted);margin-bottom:8px}
.swatches{display:flex;gap:4px}
.swatches span{width:22px;height:22px;border-radius:4px;border:1px solid rgba(0,0,0,.1)}

/* ── Poster Grid ───────────────────────────────────────────────── */
.poster-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px;margin-top:16px}
.poster-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
             overflow:hidden;transition:transform .15s}
.poster-card:hover{transform:translateY(-2px)}
.poster-card img{width:100%;display:block}
.poster-card .info{padding:10px 12px;font-size:.8rem;color:var(--text-muted);
                   display:flex;justify-content:space-between;align-items:center}

/* ── Result Area ───────────────────────────────────────────────── */
#result{margin-top:24px}
.result-item{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
             margin-bottom:20px;overflow:hidden}
.result-item img{width:100%;max-height:70vh;object-fit:contain;display:block;background:#eee}
.result-item .bar{padding:12px 16px;display:flex;justify-content:space-between;align-items:center;
                  border-top:1px solid var(--border)}
.result-item .bar .title{font-weight:600}

/* ── Progress ──────────────────────────────────────────────────── */
#progress{display:none;margin-top:16px}
.progress-bar{height:6px;background:var(--border);border-radius:3px;overflow:hidden}
.progress-bar .fill{height:100%;background:var(--accent);transition:width .3s;width:0%}
.progress-text{font-size:.8rem;color:var(--text-muted);margin-top:6px;text-align:center}

/* ── Spinner ───────────────────────────────────────────────────── */
@keyframes spin{to{transform:rotate(360deg)}}
.spinner{width:18px;height:18px;border:2px solid var(--border);border-top-color:var(--accent);
         border-radius:50%;animation:spin .6s linear infinite;display:inline-block}

/* ── Dark-mode toggle ──────────────────────────────────────────── */
.theme-toggle{
  background:none;border:1px solid var(--border);border-radius:var(--radius);
  cursor:pointer;padding:6px 8px;font-size:1.1rem;line-height:1;
  color:var(--text);transition:border-color .2s;
}
.theme-toggle:hover{border-color:var(--accent)}

/* ── Responsive ────────────────────────────────────────────────── */
@media(max-width:800px){
  body{flex-direction:column}
  .sidebar{width:100%;min-width:0;border-right:none;border-bottom:1px solid var(--border)}
  .main{padding:20px}
  .guide{grid-template-columns:1fr}
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

  <!-- Actions -->
  <div style="margin-top:auto;display:flex;flex-direction:column;gap:8px">
    <button class="btn btn-primary" id="btn-generate" onclick="generate(false)">
      Generate Poster
    </button>
    <button class="btn btn-secondary" id="btn-all" onclick="generate(true)">
      Generate All Themes
    </button>
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

  <!-- Theme Gallery -->
  <h3 style="margin-bottom:12px">Available Themes</h3>
  <div class="gallery">
    {% for key, t in themes.items() %}
    <div class="gallery-card">
      <div class="name">{{ t.get('name', key) }}</div>
      <div class="desc">{{ t.get('description', '') }}</div>
      <div class="swatches">
        <span style="background:{{ t.bg }}" title="Background"></span>
        <span style="background:{{ t.text }}" title="Text"></span>
        <span style="background:{{ t.get('road_motorway','#888') }}" title="Roads"></span>
        <span style="background:{{ t.water }}" title="Water"></span>
        <span style="background:{{ t.parks }}" title="Parks"></span>
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
      <img src="/posters/{{ name }}" alt="{{ name }}" loading="lazy">
      <div class="info">
        <span>{{ name }}</span>
        <a href="/posters/{{ name }}" download class="btn btn-sm btn-secondary">Download</a>
      </div>
    </div>
    {% endfor %}
  </div>
  {% endif %}

  <!-- Progress & Results (shown dynamically) -->
  <div id="progress">
    <div class="progress-bar"><div class="fill" id="progress-fill"></div></div>
    <div class="progress-text" id="progress-text">Preparing...</div>
  </div>
  <div id="result"></div>
</main>

<!-- ═══════════════════════════ JS ════════════════════════════════ -->
<script>
/* ── Distance slider ───────────────────────────────────────────── */
const distSlider = document.getElementById('distance');
const distVal = document.getElementById('distance-val');
distSlider.addEventListener('input', () => {
  distVal.textContent = Number(distSlider.value).toLocaleString('fr-FR') + ' m';
});

/* ── Theme selector ────────────────────────────────────────────── */
const themeDescs = {{ themes | tojson }};
document.querySelectorAll('.theme-chip').forEach(chip => {
  chip.addEventListener('click', () => {
    document.querySelectorAll('.theme-chip').forEach(c => c.classList.remove('active'));
    chip.classList.add('active');
    const key = chip.dataset.theme;
    document.getElementById('theme-desc').textContent =
      (themeDescs[key] && themeDescs[key].description) || '';
  });
});

function getSelectedTheme() {
  const active = document.querySelector('.theme-chip.active');
  return active ? active.dataset.theme : 'terracotta';
}

/* ── Generate ──────────────────────────────────────────────────── */
async function generate(allThemes) {
  const city = document.getElementById('city').value.trim();
  const country = document.getElementById('country').value.trim();
  if (!city || !country) {
    alert('City and Country are required.');
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

  // UI: disable buttons, show progress
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
  progressText.textContent = 'Generating poster... this may take 30-60s for new cities.';
  resultDiv.innerHTML = '';

  try {
    const resp = await fetch('/api/generate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });

    progressFill.style.width = '100%';

    if (!resp.ok) {
      const err = await resp.json();
      progressText.textContent = 'Error: ' + (err.error || 'Unknown error');
      return;
    }

    const data = await resp.json();
    progressText.textContent = 'Done! ' + data.results.length + ' poster(s) generated.';

    // Render results
    let html = '';
    for (const r of data.results) {
      if (r.error) {
        html += '<div class="result-item"><div class="bar" style="color:#c33">Error (' +
                r.theme + '): ' + r.error + '</div></div>';
      } else {
        const ext = payload.format;
        const isPng = ext === 'png';
        html += '<div class="result-item">';
        if (isPng) {
          html += '<img src="/posters/' + r.file + '" alt="' + r.file + '">';
        } else {
          html += '<div style="padding:40px;text-align:center;color:var(--text-muted)">' +
                  ext.toUpperCase() + ' generated &mdash; use the download button below.</div>';
        }
        html += '<div class="bar"><span class="title">' + city + ', ' + country +
                ' &mdash; ' + r.theme + '</span>' +
                '<a href="/posters/' + r.file + '" download class="btn btn-sm btn-primary">' +
                'Download ' + ext.toUpperCase() + '</a></div></div>';
      }
    }
    resultDiv.innerHTML = html;

  } catch (e) {
    progressText.textContent = 'Network error: ' + e.message;
  } finally {
    btnGen.disabled = btnAll.disabled = false;
    btnGen.innerHTML = 'Generate Poster';
  }
}

/* ── Dark mode toggle ──────────────────────────────────────────── */
function applyTheme(dark) {
  document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
  document.getElementById('toggle-icon').innerHTML = dark ? '&#9788;' : '&#9789;';
}
function toggleDark() {
  const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
  const next = !isDark;
  applyTheme(next);
  localStorage.setItem('mtp-dark', next ? '1' : '0');
}
// Restore preference on load
(function(){
  const saved = localStorage.getItem('mtp-dark');
  if (saved === '1') applyTheme(true);
  else if (saved === null && window.matchMedia('(prefers-color-scheme: dark)').matches) applyTheme(true);
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    POSTERS_DIR.mkdir(exist_ok=True)
    print("MapToPoster GUI running at http://localhost:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
