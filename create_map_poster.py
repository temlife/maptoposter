#!/usr/bin/env python3
"""
City Map Poster Generator

This module generates beautiful, minimalist map posters for any city in the world.
It fetches OpenStreetMap data using OSMnx, applies customizable themes, and creates
high-quality poster-ready images with roads, water features, and parks.
"""

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
import os
import pickle
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import cast

import matplotlib.colors as mcolors
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
import numpy as np
import osmnx as ox
from geopandas import GeoDataFrame
from geopy.geocoders import Nominatim
from lat_lon_parser import parse
from matplotlib.font_manager import FontProperties
from networkx import MultiDiGraph
from shapely.geometry import Point
from tqdm import tqdm

from font_management import load_fonts


class CacheError(Exception):
    """Raised when a cache operation fails."""


CACHE_DIR_PATH = os.environ.get("CACHE_DIR", "cache")
CACHE_DIR = Path(CACHE_DIR_PATH)
CACHE_DIR.mkdir(exist_ok=True)

THEMES_DIR = "themes"
FONTS_DIR = "fonts"
POSTERS_DIR = "posters"

FILE_ENCODING = "utf-8"

CACHE_TTL_DAYS = int(os.environ.get("CACHE_TTL_DAYS", "30"))
CACHE_MAX_SIZE_MB = int(os.environ.get("CACHE_MAX_SIZE_MB", "500"))

# In-memory cache to avoid repeated pickle.load from disk within the same process
_mem_cache: dict = {}
_MEM_CACHE_MAX = 64

# OSM feature layers: name → tags dict for ox.features_from_point
LAYER_TAGS: dict[str, dict] = {
    "water": {"natural": ["water", "bay", "strait"], "waterway": "riverbank"},
    "waterways": {"waterway": ["river", "stream", "canal"]},
    "parks": {"leisure": "park", "landuse": "grass"},
    "forests": {"landuse": "forest", "natural": "wood"},
    "beaches": {"natural": ["beach", "sand", "wetland"]},
    "landuse": {"landuse": ["residential", "retail", "commercial", "industrial"]},
    "buildings": {"building": True},
    "railways": {"railway": ["rail", "subway", "light_rail", "tram"]},
}
# Default layers fetched when none specified (CLI). "landuse" is opt-in via GUI.
DEFAULT_LAYERS = ["water", "waterways", "parks", "forests", "beaches", "buildings", "railways"]


@dataclass
class _CacheEntry:
    data: object
    timestamp: float


FONTS = load_fonts()


def _cache_path(key: str) -> str:
    """
    Generate a safe cache file path from a cache key.

    Args:
        key: Cache key identifier

    Returns:
        Path to cache file with .pkl extension
    """
    safe = key.replace(os.sep, "_")
    return os.path.join(CACHE_DIR, f"{safe}.pkl")


def cache_get(key: str):
    """
    Retrieve a cached object by key. Checks in-memory cache first, then disk.
    Returns None if not found or expired.

    Args:
        key: Cache key identifier

    Returns:
        Cached object if found and not expired, None otherwise

    Raises:
        CacheError: If cache read operation fails
    """
    # 1. In-memory hit (avoids pickle.load from disk)
    if key in _mem_cache:
        entry = _mem_cache[key]
        if isinstance(entry, _CacheEntry):
            if (time.time() - entry.timestamp) / 86400 <= CACHE_TTL_DAYS:
                return entry.data
            del _mem_cache[key]
        else:
            return entry  # legacy

    # 2. Disk hit
    try:
        path = _cache_path(key)
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            entry = pickle.load(f)
        # Populate in-memory cache
        if len(_mem_cache) >= _MEM_CACHE_MAX:
            _mem_cache.pop(next(iter(_mem_cache)))
        _mem_cache[key] = entry
        if isinstance(entry, _CacheEntry):
            if (time.time() - entry.timestamp) / 86400 > CACHE_TTL_DAYS:
                os.remove(path)
                del _mem_cache[key]
                return None
            return entry.data
        # Legacy format without TTL: return as-is
        return entry
    except Exception as e:
        raise CacheError(f"Cache read failed: {e}") from e


def cache_set(key: str, value):
    """
    Store an object in the in-memory and disk cache with a timestamp for TTL support.

    Args:
        key: Cache key identifier
        value: Object to cache (must be picklable)

    Raises:
        CacheError: If cache write operation fails
    """
    entry = _CacheEntry(data=value, timestamp=time.time())
    # Store in memory first
    if len(_mem_cache) >= _MEM_CACHE_MAX:
        _mem_cache.pop(next(iter(_mem_cache)))
    _mem_cache[key] = entry
    # Persist to disk
    try:
        if not os.path.exists(CACHE_DIR):
            os.makedirs(CACHE_DIR)
        _cache_evict_if_needed()
        path = _cache_path(key)
        with open(path, "wb") as f:
            pickle.dump(entry, f, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as e:
        raise CacheError(f"Cache write failed: {e}") from e


def _cache_evict_if_needed():
    """Remove oldest cache entries if total size exceeds CACHE_MAX_SIZE_MB."""
    max_bytes = CACHE_MAX_SIZE_MB * 1024 * 1024
    files = list(CACHE_DIR.glob("*.pkl"))
    if not files:
        return
    total = sum(f.stat().st_size for f in files)
    if total <= max_bytes:
        return
    files.sort(key=lambda f: f.stat().st_mtime)
    for f in files:
        if total <= max_bytes:
            break
        size = f.stat().st_size
        f.unlink(missing_ok=True)
        total -= size


# Font loading now handled by font_management.py module


def is_latin_script(text):
    """
    Check if text is primarily Latin script.
    Used to determine if letter-spacing should be applied to city names.

    :param text: Text to analyze
    :return: True if text is primarily Latin script, False otherwise
    """
    if not text:
        return True

    latin_count = 0
    total_alpha = 0

    for char in text:
        if char.isalpha():
            total_alpha += 1
            # Latin Unicode ranges:
            # - Basic Latin: U+0000 to U+007F
            # - Latin-1 Supplement: U+0080 to U+00FF
            # - Latin Extended-A: U+0100 to U+017F
            # - Latin Extended-B: U+0180 to U+024F
            if ord(char) < 0x250:
                latin_count += 1

    # If no alphabetic characters, default to Latin (numbers, symbols, etc.)
    if total_alpha == 0:
        return True

    # Consider it Latin if >80% of alphabetic characters are Latin
    return (latin_count / total_alpha) > 0.8


def generate_output_filename(city, theme_name, output_format):
    """
    Generate unique output filename with city, theme, and datetime.
    """
    if not os.path.exists(POSTERS_DIR):
        os.makedirs(POSTERS_DIR)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    city_slug = city.lower().replace(" ", "_")
    ext = output_format.lower()
    filename = f"{city_slug}_{theme_name}_{timestamp}.{ext}"
    return os.path.join(POSTERS_DIR, filename)


def get_available_themes():
    """
    Scans the themes directory and returns a list of available theme names.
    """
    if not os.path.exists(THEMES_DIR):
        os.makedirs(THEMES_DIR)
        return []

    themes = []
    for file in sorted(os.listdir(THEMES_DIR)):
        if file.endswith(".json"):
            theme_name = file[:-5]  # Remove .json extension
            themes.append(theme_name)
    return themes


def load_theme(theme_name="terracotta"):
    """
    Load theme from JSON file in themes directory.
    """
    theme_file = os.path.join(THEMES_DIR, f"{theme_name}.json")

    if not os.path.exists(theme_file):
        print(f"⚠ Theme file '{theme_file}' not found. Using default terracotta theme.")
        # Fallback to embedded terracotta theme
        return {
            "name": "Terracotta",
            "description": "Mediterranean warmth - burnt orange and clay tones on cream",
            "bg": "#F5EDE4",
            "text": "#8B4513",
            "gradient_color": "#F5EDE4",
            "water": "#A8C4C4",
            "parks": "#E8E0D0",
            "road_motorway": "#A0522D",
            "road_primary": "#B8653A",
            "road_secondary": "#C9846A",
            "road_tertiary": "#D9A08A",
            "road_residential": "#E5C4B0",
            "road_default": "#D9A08A",
        }

    with open(theme_file, "r", encoding=FILE_ENCODING) as f:
        theme = json.load(f)
        print(f"✓ Loaded theme: {theme.get('name', theme_name)}")
        if "description" in theme:
            print(f"  {theme['description']}")
        return theme


# Load theme (can be changed via command line or input)
THEME = dict[str, str]()  # Will be loaded later


def create_gradient_fade(ax, color, location="bottom", zorder=10, fade_pct=25):
    """
    Creates a fade effect at the top or bottom of the map.

    Args:
        fade_pct: Percentage of the axis height covered by the fade (default: 25).
    """
    vals = np.linspace(0, 1, 256).reshape(-1, 1)
    gradient = np.hstack((vals, vals))

    rgb = mcolors.to_rgb(color)
    my_colors = np.zeros((256, 4))
    my_colors[:, 0] = rgb[0]
    my_colors[:, 1] = rgb[1]
    my_colors[:, 2] = rgb[2]

    frac = max(5, min(50, fade_pct)) / 100.0
    if location == "bottom":
        my_colors[:, 3] = np.linspace(1, 0, 256)
        extent_y_start = 0
        extent_y_end = frac
    else:
        my_colors[:, 3] = np.linspace(0, 1, 256)
        extent_y_start = 1.0 - frac
        extent_y_end = 1.0

    custom_cmap = mcolors.ListedColormap(my_colors)

    xlim = ax.get_xlim()
    ylim = ax.get_ylim()
    y_range = ylim[1] - ylim[0]

    y_bottom = ylim[0] + y_range * extent_y_start
    y_top = ylim[0] + y_range * extent_y_end

    ax.imshow(
        gradient,
        extent=[xlim[0], xlim[1], y_bottom, y_top],
        aspect="auto",
        cmap=custom_cmap,
        zorder=zorder,
        origin="lower",
    )


def _darken_color(hex_color: str, amount: float = 0.18) -> tuple:
    """Return a slightly darkened version of a hex color as an RGB tuple."""
    r, g, b = mcolors.to_rgb(hex_color)
    return (max(0.0, r - amount), max(0.0, g - amount), max(0.0, b - amount))


def add_vignette(ax, xlim, ylim, color: str, strength: float = 0.55):
    """Radial darkening toward edges — gives a 'focused lens' poster look."""
    size = 256
    x = np.linspace(-1, 1, size)
    y = np.linspace(-1, 1, size)
    X, Y = np.meshgrid(x, y)
    r = np.sqrt((X * 0.85) ** 2 + (Y * 0.85) ** 2)
    alpha = np.clip(r ** 2 * strength, 0, 0.80)
    rgb = mcolors.to_rgb(color)
    img = np.zeros((size, size, 4))
    img[:, :, :3] = rgb
    img[:, :, 3] = alpha
    ax.imshow(img, extent=[xlim[0], xlim[1], ylim[0], ylim[1]],
              aspect="auto", zorder=9, interpolation="bilinear", origin="lower")


def add_grain(ax, xlim, ylim, strength: float = 0.05):
    """Paper/film grain overlay — adds an organic, printed texture."""
    rng = np.random.default_rng(42)  # fixed seed → reproducible output
    size = 512
    noise = rng.standard_normal((size, size))
    alpha = np.clip(np.abs(noise) * strength, 0, 0.10)
    img = np.zeros((size, size, 4))  # black grain dots
    img[:, :, 3] = alpha
    ax.imshow(img, extent=[xlim[0], xlim[1], ylim[0], ylim[1]],
              aspect="auto", zorder=8, interpolation="nearest", origin="lower")


_EDGE_COLOR_MAP = {
    "motorway": "road_motorway", "motorway_link": "road_motorway",
    "trunk": "road_primary", "trunk_link": "road_primary",
    "primary": "road_primary", "primary_link": "road_primary",
    "secondary": "road_secondary", "secondary_link": "road_secondary",
    "tertiary": "road_tertiary", "tertiary_link": "road_tertiary",
    "residential": "road_residential", "living_street": "road_residential",
    "unclassified": "road_residential",
}
_EDGE_WIDTH_MAP = {
    "motorway": 1.2, "motorway_link": 1.2,
    "trunk": 1.0, "trunk_link": 1.0, "primary": 1.0, "primary_link": 1.0,
    "secondary": 0.8, "secondary_link": 0.8,
    "tertiary": 0.6, "tertiary_link": 0.6,
}


def get_edge_colors_and_widths(g, theme, dist: float = 18000):
    """
    Returns (colors, widths) lists for all edges in a single pass.
    Road widths scale with the map radius so small areas look as
    detailed as large ones.
    """
    # Narrower dist → thicker lines (more detail visible); clamp to [0.5, 2.0]
    width_scale = max(0.5, min(2.0, 15000 / dist))
    default_color = theme["road_default"]
    colors, widths = [], []
    for _u, _v, data in g.edges(data=True):
        hw = data.get("highway", "unclassified")
        if isinstance(hw, list):
            hw = hw[0] if hw else "unclassified"
        colors.append(theme.get(_EDGE_COLOR_MAP.get(hw, ""), default_color))
        widths.append(_EDGE_WIDTH_MAP.get(hw, 0.4) * width_scale)
    return colors, widths


# Keep individual functions as thin wrappers for backward compatibility
def get_edge_colors_by_type(g, theme):
    return get_edge_colors_and_widths(g, theme)[0]


def get_edge_widths_by_type(g):
    # Reuse the combined function with a minimal theme
    widths = []
    for _u, _v, data in g.edges(data=True):
        hw = data.get("highway", "unclassified")
        if isinstance(hw, list):
            hw = hw[0] if hw else "unclassified"
        widths.append(_EDGE_WIDTH_MAP.get(hw, 0.4))
    return widths


def get_coordinates(city, country):
    """
    Fetches coordinates for a given city and country using geopy.
    Includes rate limiting to be respectful to the geocoding service.
    """
    coords = f"coords_{city.lower()}_{country.lower()}"
    cached = cache_get(coords)
    if cached:
        print(f"✓ Using cached coordinates for {city}, {country}")
        return cached

    print("Looking up coordinates...")
    geolocator = Nominatim(user_agent="city_map_poster", timeout=10)

    # Add a small delay to respect Nominatim's usage policy
    time.sleep(1)

    try:
        location = geolocator.geocode(f"{city}, {country}")
    except Exception as e:
        raise ValueError(f"Geocoding failed for {city}, {country}: {e}") from e

    # If geocode returned a coroutine in some environments, run it to get the result.
    if asyncio.iscoroutine(location):
        try:
            location = asyncio.run(location)
        except RuntimeError as exc:
            # If an event loop is already running, try using it to complete the coroutine.
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Running event loop in the same thread; raise a clear error.
                raise RuntimeError(
                    "Geocoder returned a coroutine while an event loop is already running. "
                    "Run this script in a synchronous environment."
                ) from exc
            location = loop.run_until_complete(location)

    if location:
        # Use getattr to safely access address (helps static analyzers)
        addr = getattr(location, "address", None)
        if addr:
            print(f"✓ Found: {addr}")
        else:
            print("✓ Found location (address not available)")
        print(f"✓ Coordinates: {location.latitude}, {location.longitude}")
        try:
            cache_set(coords, (location.latitude, location.longitude))
        except CacheError as e:
            print(e)
        return (location.latitude, location.longitude)

    raise ValueError(f"Could not find coordinates for {city}, {country}")


def get_crop_limits(g_proj, center_lat_lon, fig, dist):
    """
    Crop inward to preserve aspect ratio while guaranteeing
    full coverage of the requested radius.
    """
    lat, lon = center_lat_lon

    # Project center point into graph CRS
    center = (
        ox.projection.project_geometry(
            Point(lon, lat),
            crs="EPSG:4326",
            to_crs=g_proj.graph["crs"]
        )[0]
    )
    center_x, center_y = center.x, center.y

    fig_width, fig_height = fig.get_size_inches()
    aspect = fig_width / fig_height

    # Start from the *requested* radius
    half_x = dist
    half_y = dist

    # Cut inward to match aspect
    if aspect > 1:  # landscape → reduce height
        half_y = half_x / aspect
    else:  # portrait → reduce width
        half_x = half_y * aspect

    return (
        (center_x - half_x, center_x + half_x),
        (center_y - half_y, center_y + half_y),
    )


def _round_coord(value: float, decimals: int = 3) -> float:
    """Round coordinate for cache key (~100m precision at equator)."""
    return round(value, decimals)


def fetch_graph(point, dist) -> MultiDiGraph | None:
    """
    Fetch street network graph from OpenStreetMap.

    Uses caching to avoid redundant downloads. Fetches all network types
    within the specified distance from the center point.

    Args:
        point: (latitude, longitude) tuple for center point
        dist: Distance in meters from center point

    Returns:
        MultiDiGraph of street network, or None if fetch fails
    """
    lat, lon = _round_coord(point[0]), _round_coord(point[1])
    graph = f"graph_{lat}_{lon}_{dist}"
    cached = cache_get(graph)
    if cached is not None:
        print("✓ Using cached street network")
        return cast(MultiDiGraph, cached)

    try:
        g = ox.graph_from_point(point, dist=dist, dist_type='bbox', network_type='all', truncate_by_edge=True)
        # Rate limit between requests
        time.sleep(0.5)
        try:
            cache_set(graph, g)
        except CacheError as e:
            print(e)
        return g
    except Exception as e:
        print(f"OSMnx error while fetching graph: {e}")
        return None


def fetch_features(point, dist, tags, name) -> GeoDataFrame | None:
    """
    Fetch geographic features (water, parks, etc.) from OpenStreetMap.

    Uses caching to avoid redundant downloads. Fetches features matching
    the specified OSM tags within distance from center point.

    Args:
        point: (latitude, longitude) tuple for center point
        dist: Distance in meters from center point
        tags: Dictionary of OSM tags to filter features
        name: Name for this feature type (for caching and logging)

    Returns:
        GeoDataFrame of features, or None if fetch fails
    """
    lat, lon = _round_coord(point[0]), _round_coord(point[1])
    tag_str = "_".join(sorted(tags.keys()))
    features = f"{name}_{lat}_{lon}_{dist}_{tag_str}"
    cached = cache_get(features)
    if cached is not None:
        print(f"✓ Using cached {name}")
        return cast(GeoDataFrame, cached)

    try:
        data = ox.features_from_point(point, tags=tags, dist=dist)
        # Rate limit between requests
        time.sleep(0.3)
        try:
            cache_set(features, data)
        except CacheError as e:
            print(e)
        return data
    except Exception as e:
        print(f"OSMnx error while fetching features: {e}")
        return None


def fetch_map_data(point: tuple, compensated_dist: float, layers: list | None = None):
    """
    Fetch all OSM data required for poster generation.

    Returns (graph, features_dict). Pass result as prefetched=(graph, features_dict)
    to create_poster() to render multiple themes without re-downloading OSM data.

    Args:
        point: (latitude, longitude) tuple
        compensated_dist: Adjusted distance in meters (accounting for poster aspect ratio)
        layers: List of layer names to fetch (default: all layers in DEFAULT_LAYERS)

    Returns:
        Tuple of (graph, features_dict) where features_dict maps layer name → GeoDataFrame

    Raises:
        RuntimeError: If street network data cannot be retrieved
    """
    if layers is None:
        layers = DEFAULT_LAYERS

    valid_layers = [(name, LAYER_TAGS[name]) for name in layers if name in LAYER_TAGS]

    with tqdm(
        total=1 + len(valid_layers),
        desc="Fetching map data",
        unit="step",
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt}",
    ) as pbar:
        pbar.set_description("Downloading street network")
        g = fetch_graph(point, compensated_dist)
        if g is None:
            raise RuntimeError("Failed to retrieve street network data.")
        pbar.update(1)

        features_dict: dict = {}
        # Fetch all feature layers in parallel — each is an independent OSM query
        with ThreadPoolExecutor(max_workers=len(valid_layers) or 1) as executor:
            futures = {
                executor.submit(fetch_features, point, compensated_dist, tags, name): name
                for name, tags in valid_layers
            }
            for future in as_completed(futures):
                name = futures[future]
                features_dict[name] = future.result()
                pbar.update(1)

    print("✓ All data retrieved successfully!")
    return g, features_dict


def create_poster(
    city,
    country,
    point,
    dist,
    output_file,
    output_format,
    width=12,
    height=16,
    country_label=None,
    name_label=None,
    display_city=None,
    display_country=None,
    fonts=None,
    theme=None,
    prefetched=None,
    layers=None,
    dpi=300,
    layout="bottom",
    tagline=None,
    separator="line",
    vignette=True,
    grain=False,
    show_country=True,
    show_coords=True,
):
    """
    Generate a complete map poster with roads, water, parks, and typography.

    Creates a high-quality poster by fetching OSM data, rendering map layers,
    applying the current theme, and adding text labels with coordinates.

    Args:
        city: City name for display on poster
        country: Country name for display on poster
        point: (latitude, longitude) tuple for map center
        dist: Map radius in meters
        output_file: Path where poster will be saved
        output_format: File format ('png', 'svg', or 'pdf')
        width: Poster width in inches (default: 12)
        height: Poster height in inches (default: 16)
        country_label: Optional override for country text on poster
        theme: Theme dict to use; falls back to global THEME if None
        prefetched: Optional (graph, features_dict) tuple from fetch_map_data()
                    to skip OSM downloads when rendering multiple themes
        layers: List of layer names to render (default: all DEFAULT_LAYERS)

    Raises:
        RuntimeError: If street network data cannot be retrieved
    """
    # Use explicit theme or fall back to global for backward compatibility
    theme = theme or THEME

    if layers is None:
        layers = DEFAULT_LAYERS

    # Handle display names for i18n support
    # Priority: display_city/display_country > name_label/country_label > city/country
    display_city = display_city or name_label or city
    display_country = display_country or country_label or country

    print(f"\nGenerating map for {city}, {country}...")

    # 1. Acquire OSM data — from pre-fetched cache or fresh download
    compensated_dist = dist * (max(height, width) / min(height, width)) / 4
    if prefetched is not None:
        g, features_dict = prefetched
    else:
        g, features_dict = fetch_map_data(point, compensated_dist, layers=layers)

    # 2. Setup Plot — use Figure API directly to avoid pyplot global state (thread-safe)
    print("Rendering map...")
    fig = Figure(figsize=(width, height), facecolor=theme["bg"])
    ax = fig.add_axes([0.0, 0.0, 1.0, 1.0])
    ax.set_facecolor(theme["bg"])

    # Project graph to a metric CRS so distances and aspect are linear (meters)
    g_proj = ox.project_graph(g)

    def _project_gdf(gdf, simplify: float = 0.0):
        try:
            projected = ox.projection.project_gdf(gdf)
        except Exception:
            projected = gdf.to_crs(g_proj.graph['crs'])
        if simplify > 0:
            projected = projected.copy()
            projected["geometry"] = projected["geometry"].simplify(simplify, preserve_topology=True)
        return projected

    # 3. Plot Layers
    # Layer 1: Polygons (filter to only plot polygon/multipolygon geometries, not points)
    # Layer 1a: Land use (subtle urban texture, below everything else)
    landuse = features_dict.get("landuse")
    if landuse is not None and not landuse.empty:
        lu_polys = landuse[landuse.geometry.type.isin(["Polygon", "MultiPolygon"])]
        if not lu_polys.empty:
            lu_color = theme.get("landuse", _darken_color(theme["bg"], 0.04))
            _project_gdf(lu_polys, simplify=5).plot(ax=ax, facecolor=lu_color, edgecolor="none", alpha=0.6, zorder=0.2)

    # Beaches (sand/wetland polygons, below water)
    beaches = features_dict.get("beaches")
    if beaches is not None and not beaches.empty:
        beach_polys = beaches[beaches.geometry.type.isin(["Polygon", "MultiPolygon"])]
        if not beach_polys.empty:
            beach_color = theme.get("beaches", _darken_color(theme["bg"], 0.06))
            _project_gdf(beach_polys).plot(ax=ax, facecolor=beach_color, edgecolor="none", alpha=0.7, zorder=0.3)

    water = features_dict.get("water")
    if water is not None and not water.empty:
        water_polys = water[water.geometry.type.isin(["Polygon", "MultiPolygon"])]
        if not water_polys.empty:
            water_edge = theme.get("water_edge", _darken_color(theme["water"], 0.12))
            _project_gdf(water_polys).plot(
                ax=ax, facecolor=theme["water"], edgecolor=water_edge, linewidth=0.6, zorder=0.5
            )

    # Forests (larger wooded areas, between water and parks)
    forests = features_dict.get("forests")
    if forests is not None and not forests.empty:
        forest_polys = forests[forests.geometry.type.isin(["Polygon", "MultiPolygon"])]
        if not forest_polys.empty:
            forest_color = theme.get("forests", _darken_color(theme["parks"], 0.05))
            _project_gdf(forest_polys, simplify=3).plot(ax=ax, facecolor=forest_color, edgecolor="none", alpha=0.8, zorder=0.6)

    parks = features_dict.get("parks")
    if parks is not None and not parks.empty:
        parks_polys = parks[parks.geometry.type.isin(["Polygon", "MultiPolygon"])]
        if not parks_polys.empty:
            _project_gdf(parks_polys).plot(ax=ax, facecolor=theme["parks"], edgecolor="none", zorder=0.8)

    buildings = features_dict.get("buildings")
    if buildings is not None and not buildings.empty:
        bld_polys = buildings[buildings.geometry.type.isin(["Polygon", "MultiPolygon"])]
        if not bld_polys.empty:
            bld_color = theme.get("buildings", theme.get("road_residential", theme["road_tertiary"]))
            _project_gdf(bld_polys, simplify=2).plot(ax=ax, facecolor=bld_color, edgecolor="none", alpha=0.5, zorder=0.9)

    # Layer 2: Roads with hierarchy coloring (single pass for colors + widths)
    print("Applying road hierarchy colors...")
    edge_colors, edge_widths = get_edge_colors_and_widths(g_proj, theme, dist=compensated_dist)

    # Determine cropping limits to maintain the poster aspect ratio
    crop_xlim, crop_ylim = get_crop_limits(g_proj, point, fig, compensated_dist)

    # Shift the viewport so the city centre sits above the text zone rather
    # than visually centred behind it.  We nudge the crop window toward the
    # text side (down for bottom layout, up for top) so more map is visible
    # on the opposite side.  10% of the half-height is a subtle but clear shift.
    _half_h = (crop_ylim[1] - crop_ylim[0]) / 2
    _shift = _half_h * 0.18
    if layout == "top":
        crop_ylim = (crop_ylim[0] + _shift, crop_ylim[1] + _shift)
    else:
        crop_ylim = (crop_ylim[0] - _shift, crop_ylim[1] - _shift)
    ox.plot_graph(
        g_proj, ax=ax, bgcolor=theme["bg"],
        node_size=0,
        edge_color=edge_colors,
        edge_linewidth=edge_widths,
        show=False,
        close=False,
    )
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlim(crop_xlim)
    ax.set_ylim(crop_ylim)

    # Layer 2b: Waterways (linear rivers, streams, canals)
    waterways = features_dict.get("waterways")
    if waterways is not None and not waterways.empty:
        ww_lines = waterways[waterways.geometry.type.isin(["LineString", "MultiLineString"])]
        if not ww_lines.empty:
            ww_color = theme.get("waterways", theme["water"])
            _project_gdf(ww_lines).plot(ax=ax, color=ww_color, linewidth=0.5, alpha=0.8, zorder=1.5)

    # Layer 2c: Railways
    railways = features_dict.get("railways")
    if railways is not None and not railways.empty:
        rail_lines = railways[railways.geometry.type.isin(["LineString", "MultiLineString"])]
        if not rail_lines.empty:
            rail_color = theme.get("railways", theme.get("road_primary", "#888"))
            _project_gdf(rail_lines).plot(ax=ax, color=rail_color, linewidth=0.8, alpha=0.7, zorder=2.5)

    # Layer 3: Gradients — text side gets the strong fade, opposite gets a thin frame fade
    fade_pct = theme.get("gradient_pct", 25)
    if layout == "top":
        create_gradient_fade(ax, theme["gradient_color"], location="top", zorder=10, fade_pct=fade_pct)
        create_gradient_fade(ax, theme["gradient_color"], location="bottom", zorder=10, fade_pct=fade_pct)
    else:  # bottom (default)
        create_gradient_fade(ax, theme["gradient_color"], location="bottom", zorder=10, fade_pct=fade_pct)
        create_gradient_fade(ax, theme["gradient_color"], location="top", zorder=10, fade_pct=fade_pct)

    # Layer 4: Vignette + grain (applied after crop limits are set)
    if vignette:
        add_vignette(ax, crop_xlim, crop_ylim, theme.get("gradient_color", theme["bg"]))
    if grain:
        add_grain(ax, crop_xlim, crop_ylim)

    # 4. Typography
    scale_factor = min(height, width) / 12.0
    base_main = 60
    base_sub = 22
    base_coords = 14
    base_attr = 8

    active_fonts = fonts or FONTS
    if active_fonts:
        font_sub = FontProperties(fname=active_fonts["light"], size=base_sub * scale_factor)
        font_coords = FontProperties(fname=active_fonts["regular"], size=base_coords * scale_factor)
        font_attr = FontProperties(fname=active_fonts["light"], size=base_attr * scale_factor)
        if tagline:
            font_tagline = FontProperties(fname=active_fonts["light"], size=base_sub * 0.75 * scale_factor)
    else:
        font_sub = FontProperties(family="monospace", weight="normal", size=base_sub * scale_factor)
        font_coords = FontProperties(family="monospace", size=base_coords * scale_factor)
        font_attr = FontProperties(family="monospace", size=base_attr * scale_factor)
        if tagline:
            font_tagline = FontProperties(family="monospace", style="italic", size=base_sub * 0.75 * scale_factor)

    if is_latin_script(display_city):
        spaced_city = "  ".join(list(display_city.upper()))
    else:
        spaced_city = display_city

    base_adjusted_main = base_main * scale_factor
    city_char_count = len(display_city)
    if city_char_count > 10:
        adjusted_font_size = max(base_adjusted_main * (10 / city_char_count), 10 * scale_factor)
    else:
        adjusted_font_size = base_adjusted_main

    if active_fonts:
        font_main_adjusted = FontProperties(fname=active_fonts["bold"], size=adjusted_font_size)
    else:
        font_main_adjusted = FontProperties(family="monospace", weight="bold", size=adjusted_font_size)

    # Text block — positions adapt to which elements are visible.
    # When country and/or coords are hidden, the city name shifts toward the
    # vertical centre of the gradient zone so the layout stays balanced.
    has_tagline = bool(tagline)

    # Build adaptive positions for the bottom layout zone (≈ 0.05–0.17) or
    # top layout zone (≈ 0.83–0.95).  We compute a list of visible "slots"
    # and space them evenly within the zone.
    #
    # Slot order (bottom, innermost→outermost):
    #   city  [sep]  country  [tagline]  coords
    # For top layout the order is reversed (outermost→innermost).

    if layout == "top":
        zone_inner, zone_outer = 0.855, 0.930  # inner = closer to map edge
        sign = 1  # slots go away from map (increasing y)
    else:
        zone_inner, zone_outer = 0.125, 0.042  # inner = closer to map edge
        sign = -1  # slots go away from map (decreasing y)

    # Collect active slots — city always first, then outward
    slots: list[str] = ["city"]
    if show_country:
        slots.append("sep")
        slots.append("country")
    if has_tagline:
        slots.append("tagline")
    if show_coords:
        slots.append("coords")

    # Space slots with a preferred step, capped so they fit inside the zone.
    # The group is *centred* in the zone so that hiding elements shifts the
    # city name toward the vertical middle rather than leaving it at the edge.
    PREFERRED_STEP = 0.025
    zone_width = abs(zone_outer - zone_inner)
    zone_center = (zone_inner + zone_outer) / 2
    n = len(slots)
    step = min(PREFERRED_STEP, zone_width / max(n - 1, 1))
    first_offset = -(n - 1) / 2 * step  # offset of city from centre

    pos: dict[str, float] = {}
    for i, slot in enumerate(slots):
        pos[slot] = zone_center + (first_offset + i * step) * sign

    # Fallback values for unused slots (separator still needs y_sep)
    y_city = pos["city"]
    y_sep = pos.get("sep", zone_center)
    y_country = pos.get("country", zone_center + sign * step)
    y_tagline = pos.get("tagline", zone_center + sign * 2 * step)
    y_coords = pos.get("coords", zone_outer)

    ax.text(0.5, y_city, spaced_city,
            transform=ax.transAxes, color=theme["text"], ha="center",
            fontproperties=font_main_adjusted, zorder=11)

    if show_country:
        ax.text(0.5, y_country, display_country.upper(),
                transform=ax.transAxes, color=theme["text"], ha="center",
                fontproperties=font_sub, zorder=11)

    if has_tagline:
        ax.text(0.5, y_tagline, tagline,
                transform=ax.transAxes, color=theme["text"], ha="center",
                alpha=0.75, fontproperties=font_tagline, zorder=11)

    if show_coords:
        lat, lon = point
        coords_str = (
            f"{lat:.4f}° {'N' if lat >= 0 else 'S'} / {abs(lon):.4f}° {'E' if lon >= 0 else 'W'}"
        )
        ax.text(0.5, y_coords, coords_str,
                transform=ax.transAxes, color=theme["text"], alpha=0.7, ha="center",
                fontproperties=font_coords, zorder=11)

    # Separator between city name and country (only when country is visible)
    if not show_country:
        pass
    elif separator == "dots":
        for dx in [-0.06, -0.03, 0, 0.03, 0.06]:
            ax.plot([0.5 + dx], [y_sep], "o", transform=ax.transAxes,
                    color=theme["text"], markersize=2.0 * scale_factor, zorder=11)
    elif separator == "double":
        gap = 0.006
        for dy in (-gap, gap):
            ax.plot([0.4, 0.6], [y_sep + dy, y_sep + dy], transform=ax.transAxes,
                    color=theme["text"], linewidth=0.6 * scale_factor, alpha=0.85, zorder=11)
    else:  # "line" (default)
        ax.plot([0.4, 0.6], [y_sep, y_sep], transform=ax.transAxes,
                color=theme["text"], linewidth=1 * scale_factor, zorder=11)


    # 5. Save — check memory budget before rasterizing at high DPI
    fmt = output_format.lower()

    if fmt == "png" and dpi > 300:
        total_pixels = (width * dpi) * (height * dpi)
        # Matplotlib needs ~6x the pixel buffer during savefig compositing
        estimated_gb = (total_pixels * 4 * 6) / (1024 ** 3)
        try:
            mem_info = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES")
            avail_gb = mem_info / (1024 ** 3)
        except Exception:
            avail_gb = 8.0  # conservative fallback
        if estimated_gb > avail_gb * 0.7:
            safe_dpi = int(((avail_gb * 0.7 / 6 * (1024 ** 3)) / (width * height * 4)) ** 0.5)
            safe_dpi = max(300, (safe_dpi // 50) * 50)  # round down to nearest 50
            print(f"⚠ {dpi} DPI needs ~{estimated_gb:.1f} GB RAM but only {avail_gb:.1f} GB available.")
            print(f"  Capping to {safe_dpi} DPI to avoid crash.")
            dpi = safe_dpi

    print(f"Saving to {output_file}...")
    save_kwargs = dict(
        facecolor=theme["bg"],
        bbox_inches="tight",
        pad_inches=0.05,
    )

    # DPI matters mainly for raster formats
    if fmt == "png":
        save_kwargs["dpi"] = dpi

    fig.savefig(output_file, format=fmt, **save_kwargs)
    print(f"✓ Done! Poster saved as {output_file}")


def print_examples():
    """Print usage examples."""
    print("""
City Map Poster Generator
=========================

Usage:
  python create_map_poster.py --city <city> --country <country> [options]

Examples:
  # Iconic grid patterns
  python create_map_poster.py -c "New York" -C "USA" -t noir -d 12000           # Manhattan grid
  python create_map_poster.py -c "Barcelona" -C "Spain" -t warm_beige -d 8000   # Eixample district grid

  # Waterfront & canals
  python create_map_poster.py -c "Venice" -C "Italy" -t blueprint -d 4000       # Canal network
  python create_map_poster.py -c "Amsterdam" -C "Netherlands" -t ocean -d 6000  # Concentric canals
  python create_map_poster.py -c "Dubai" -C "UAE" -t midnight_blue -d 15000     # Palm & coastline

  # Radial patterns
  python create_map_poster.py -c "Paris" -C "France" -t pastel_dream -d 10000   # Haussmann boulevards
  python create_map_poster.py -c "Moscow" -C "Russia" -t noir -d 12000          # Ring roads

  # Organic old cities
  python create_map_poster.py -c "Tokyo" -C "Japan" -t japanese_ink -d 15000    # Dense organic streets
  python create_map_poster.py -c "Marrakech" -C "Morocco" -t terracotta -d 5000 # Medina maze
  python create_map_poster.py -c "Rome" -C "Italy" -t warm_beige -d 8000        # Ancient street layout

  # Coastal cities
  python create_map_poster.py -c "San Francisco" -C "USA" -t sunset -d 10000    # Peninsula grid
  python create_map_poster.py -c "Sydney" -C "Australia" -t ocean -d 12000      # Harbor city
  python create_map_poster.py -c "Mumbai" -C "India" -t contrast_zones -d 18000 # Coastal peninsula

  # River cities
  python create_map_poster.py -c "London" -C "UK" -t noir -d 15000              # Thames curves
  python create_map_poster.py -c "Budapest" -C "Hungary" -t copper_patina -d 8000  # Danube split

  # List themes
  python create_map_poster.py --list-themes

Options:
  --city, -c        City name (required)
  --country, -C     Country name (required)
  --country-label   Override country text displayed on poster
  --theme, -t       Theme name (default: terracotta)
  --all-themes      Generate posters for all themes
  --distance, -d    Map radius in meters (default: 18000)
  --list-themes     List all available themes

Distance guide:
  4000-6000m   Small/dense cities (Venice, Amsterdam old center)
  8000-12000m  Medium cities, focused downtown (Paris, Barcelona)
  15000-20000m Large metros, full city view (Tokyo, Mumbai)

Available themes can be found in the 'themes/' directory.
Generated posters are saved to 'posters/' directory.
""")


def list_themes():
    """List all available themes with descriptions."""
    available_themes = get_available_themes()
    if not available_themes:
        print("No themes found in 'themes/' directory.")
        return

    print("\nAvailable Themes:")
    print("-" * 60)
    for theme_name in available_themes:
        theme_path = os.path.join(THEMES_DIR, f"{theme_name}.json")
        try:
            with open(theme_path, "r", encoding=FILE_ENCODING) as f:
                theme_data = json.load(f)
                display_name = theme_data.get('name', theme_name)
                description = theme_data.get('description', '')
        except (OSError, json.JSONDecodeError):
            display_name = theme_name
            description = ""
        print(f"  {theme_name}")
        print(f"    {display_name}")
        if description:
            print(f"    {description}")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate beautiful map posters for any city",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python create_map_poster.py --city "New York" --country "USA"
  python create_map_poster.py --city "New York" --country "USA" -l 40.776676 -73.971321 --theme neon_cyberpunk
  python create_map_poster.py --city Tokyo --country Japan --theme midnight_blue
  python create_map_poster.py --city Paris --country France --theme noir --distance 15000
  python create_map_poster.py --list-themes
        """,
    )

    parser.add_argument("--city", "-c", type=str, help="City name")
    parser.add_argument("--country", "-C", type=str, help="Country name")
    parser.add_argument(
        "--latitude",
        "-lat",
        dest="latitude",
        type=str,
        help="Override latitude center point",
    )
    parser.add_argument(
        "--longitude",
        "-long",
        dest="longitude",
        type=str,
        help="Override longitude center point",
    )
    parser.add_argument(
        "--country-label",
        dest="country_label",
        type=str,
        help="Override country text displayed on poster",
    )
    parser.add_argument(
        "--theme",
        "-t",
        type=str,
        default="terracotta",
        help="Theme name (default: terracotta)",
    )
    parser.add_argument(
        "--all-themes",
        "--All-themes",
        dest="all_themes",
        action="store_true",
        help="Generate posters for all themes",
    )
    parser.add_argument(
        "--distance",
        "-d",
        type=int,
        default=18000,
        help="Map radius in meters (default: 18000)",
    )
    parser.add_argument(
        "--width",
        "-W",
        type=float,
        default=12,
        help="Image width in inches (default: 12, max: 20 )",
    )
    parser.add_argument(
        "--height",
        "-H",
        type=float,
        default=16,
        help="Image height in inches (default: 16, max: 20)",
    )
    parser.add_argument(
        "--list-themes", action="store_true", help="List all available themes"
    )
    parser.add_argument(
        "--display-city",
        "-dc",
        type=str,
        help="Custom display name for city (for i18n support)",
    )
    parser.add_argument(
        "--display-country",
        "-dC",
        type=str,
        help="Custom display name for country (for i18n support)",
    )
    parser.add_argument(
        "--font-family",
        type=str,
        help='Google Fonts family name (e.g., "Noto Sans JP", "Open Sans"). If not specified, uses local Roboto fonts.',
    )
    parser.add_argument(
        "--format",
        "-f",
        default="png",
        choices=["png", "svg", "pdf"],
        help="Output format for the poster (default: png)",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=300,
        help="Resolution in DPI (default: 300). Higher values produce larger files.",
    )
    parser.add_argument(
        "--tagline",
        type=str,
        help="Custom text below the country name",
    )
    parser.add_argument(
        "--layout",
        default="bottom",
        choices=["bottom", "top"],
        help="Text position on the poster (default: bottom)",
    )
    parser.add_argument(
        "--separator",
        default="line",
        choices=["line", "double", "dots"],
        help="Separator style between city and country (default: line)",
    )
    parser.add_argument(
        "--no-vignette",
        dest="vignette",
        action="store_false",
        help="Disable the vignette edge-darkening effect",
    )
    parser.add_argument(
        "--grain",
        action="store_true",
        help="Enable the paper grain texture overlay",
    )
    parser.add_argument(
        "--no-country",
        dest="show_country",
        action="store_false",
        help="Hide the country name on the poster",
    )
    parser.add_argument(
        "--no-coords",
        dest="show_coords",
        action="store_false",
        help="Hide the coordinates on the poster",
    )
    parser.add_argument(
        "--layers",
        nargs="+",
        default=None,
        choices=list(LAYER_TAGS.keys()),
        metavar="LAYER",
        help=f"OSM layers to render (default: all). Choices: {', '.join(LAYER_TAGS.keys())}",
    )

    args = parser.parse_args()

    # If no arguments provided, show examples
    if len(sys.argv) == 1:
        print_examples()
        sys.exit(0)

    # List themes if requested
    if args.list_themes:
        list_themes()
        sys.exit(0)

    # Validate required arguments
    if not args.city or not args.country:
        print("Error: --city and --country are required.\n")
        print_examples()
        sys.exit(1)

    # Enforce maximum dimensions
    if args.width > 20:
        print(
            f"⚠ Width {args.width} exceeds the maximum allowed limit of 20. It's enforced as max limit 20."
        )
        args.width = 20.0
    if args.height > 20:
        print(
            f"⚠ Height {args.height} exceeds the maximum allowed limit of 20. It's enforced as max limit 20."
        )
        args.height = 20.0

    available_themes = get_available_themes()
    if not available_themes:
        print("No themes found in 'themes/' directory.")
        sys.exit(1)

    if args.all_themes:
        themes_to_generate = available_themes
    else:
        if args.theme not in available_themes:
            print(f"Error: Theme '{args.theme}' not found.")
            print(f"Available themes: {', '.join(available_themes)}")
            sys.exit(1)
        themes_to_generate = [args.theme]

    print("=" * 50)
    print("City Map Poster Generator")
    print("=" * 50)

    # Load custom fonts if specified
    custom_fonts = None
    if args.font_family:
        custom_fonts = load_fonts(args.font_family)
        if not custom_fonts:
            print(f"⚠ Failed to load '{args.font_family}', falling back to Roboto")

    # Get coordinates and generate poster
    try:
        if args.latitude and args.longitude:
            lat = parse(args.latitude)
            lon = parse(args.longitude)
            coords = [lat, lon]
            print(f"✓ Coordinates: {', '.join([str(i) for i in coords])}")
        else:
            coords = get_coordinates(args.city, args.country)

        cli_layers = args.layers  # None means all layers

        if len(themes_to_generate) > 1:
            # Pre-fetch OSM data once, then render all themes in parallel
            compensated_dist = args.distance * (max(args.height, args.width) / min(args.height, args.width)) / 4
            print(f"\nFetching OSM data once (shared across {len(themes_to_generate)} themes)...")
            prefetched = fetch_map_data(tuple(coords), compensated_dist, layers=cli_layers)

            workers = min(4, len(themes_to_generate))
            print(f"Rendering {len(themes_to_generate)} themes with {workers} parallel workers...\n")

            def _render_theme(theme_name):
                t = load_theme(theme_name)
                out = generate_output_filename(args.city, theme_name, args.format)
                create_poster(
                    args.city, args.country, tuple(coords), args.distance,
                    out, args.format, args.width, args.height,
                    country_label=args.country_label,
                    display_city=args.display_city,
                    display_country=args.display_country,
                    fonts=custom_fonts,
                    theme=t,
                    prefetched=prefetched,
                    layers=cli_layers,
                    dpi=args.dpi,
                    layout=args.layout,
                    tagline=args.tagline,
                    separator=args.separator,
                    vignette=args.vignette,
                    grain=args.grain,
                    show_country=args.show_country,
                    show_coords=args.show_coords,
                )
                return out

            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(_render_theme, t): t for t in themes_to_generate}
                for future in as_completed(futures):
                    theme_name = futures[future]
                    try:
                        out = future.result()
                        print(f"✓ {theme_name}: {out}")
                    except Exception as exc:
                        print(f"✗ {theme_name}: {exc}")
        else:
            theme_name = themes_to_generate[0]
            THEME = load_theme(theme_name)
            output_file = generate_output_filename(args.city, theme_name, args.format)
            create_poster(
                args.city, args.country, tuple(coords), args.distance,
                output_file, args.format, args.width, args.height,
                country_label=args.country_label,
                display_city=args.display_city,
                display_country=args.display_country,
                fonts=custom_fonts,
                theme=THEME,
                layers=cli_layers,
                dpi=args.dpi,
                layout=args.layout,
                tagline=args.tagline,
                separator=args.separator,
                vignette=args.vignette,
                grain=args.grain,
                show_country=args.show_country,
                show_coords=args.show_coords,
            )

        print("\n" + "=" * 50)
        print("✓ Poster generation complete!")
        print("=" * 50)

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
