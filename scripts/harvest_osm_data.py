"""
harvest_osm_data.py
-------------------
Harvests safety-relevant infrastructure data from OpenStreetMap's Overpass API
for Hyderabad, Telangana and saves it to JSON files for offline use.

Data categories fetched:
  Positive (safety-boosting): Police stations, metro stations, She Teams,
    hospitals, fire stations, major commercial areas
  Negative (risk-increasing): Water bodies/lakes, rivers, industrial zones,
    isolated transit nodes

Usage:
    python scripts/harvest_osm_data.py [--output-dir backend/data]

Output files:
    osm_positive_anchors.json  — safety infrastructure
    osm_negative_anchors.json  — risk infrastructure
    osm_roads_summary.json     — road category summary (for verification)
"""

import argparse
import json
import os
import sys
import time
import requests
from datetime import datetime

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Hyderabad bounding box: south, west, north, east
HYD_BBOX = "17.20,78.20,17.60,78.65"


def query_overpass(query: str, retries: int = 3, delay: float = 5.0) -> list:
    """Execute Overpass QL query with retry logic. Returns list of elements."""
    for attempt in range(1, retries + 1):
        try:
            print(f"  → Overpass query attempt {attempt}/{retries}...")
            resp = requests.post(
                OVERPASS_URL,
                data={"data": query},
                timeout=60,
                headers={"User-Agent": "SafeGirl-Hyderabad-Harvester/1.0"}
            )
            resp.raise_for_status()
            data = resp.json()
            elements = data.get("elements", [])
            print(f"  ✓ Got {len(elements)} elements")
            return elements
        except requests.exceptions.Timeout:
            print(f"  ✗ Timeout on attempt {attempt}")
        except requests.exceptions.HTTPError as e:
            print(f"  ✗ HTTP error: {e}")
            if resp.status_code == 429:
                wait = delay * attempt * 2
                print(f"  Rate limited. Waiting {wait}s...")
                time.sleep(wait)
                continue
        except Exception as e:
            print(f"  ✗ Error: {e}")

        if attempt < retries:
            time.sleep(delay * attempt)

    return []


def extract_coords(elements: list) -> list:
    """Extracts (lat, lon, tags) from raw Overpass elements."""
    result = []
    for el in elements:
        lat, lon = None, None
        if "lat" in el and "lon" in el:
            lat, lon = el["lat"], el["lon"]
        elif "center" in el:
            lat, lon = el["center"]["lat"], el["center"]["lon"]

        if lat is not None:
            result.append({
                "lat": lat,
                "lon": lon,
                "type": el.get("type"),
                "id": el.get("id"),
                "tags": el.get("tags", {}),
            })
    return result


def harvest_police_stations() -> list:
    print("\n[1/7] Fetching police stations...")
    query = f"""
    [out:json][timeout:60];
    (
      node["amenity"="police"]({HYD_BBOX});
      way["amenity"="police"]({HYD_BBOX});
    );
    out center tags;
    """
    elements = query_overpass(query)
    data = extract_coords(elements)
    for d in data:
        d["category"] = "police_station"
        d["safety_type"] = "positive"
    return data


def harvest_metro_stations() -> list:
    print("\n[2/7] Fetching metro stations...")
    query = f"""
    [out:json][timeout:60];
    (
      node["station"="subway"]({HYD_BBOX});
      node["railway"="station"]["network"~"Hyderabad Metro",i]({HYD_BBOX});
      node["railway"="station"]["operator"~"HMRL",i]({HYD_BBOX});
    );
    out center tags;
    """
    elements = query_overpass(query)
    data = extract_coords(elements)
    for d in data:
        d["category"] = "metro_station"
        d["safety_type"] = "positive"
    return data


def harvest_hospitals() -> list:
    print("\n[3/7] Fetching hospitals...")
    query = f"""
    [out:json][timeout:60];
    (
      node["amenity"="hospital"]({HYD_BBOX});
      way["amenity"="hospital"]({HYD_BBOX});
    );
    out center tags;
    """
    elements = query_overpass(query)
    data = extract_coords(elements)
    for d in data:
        d["category"] = "hospital"
        d["safety_type"] = "positive"
    return data


def harvest_commercial_zones() -> list:
    print("\n[4/7] Fetching commercial/mall areas...")
    query = f"""
    [out:json][timeout:60];
    (
      node["shop"="mall"]({HYD_BBOX});
      way["shop"="mall"]({HYD_BBOX});
      node["landuse"="commercial"]({HYD_BBOX});
    );
    out center tags;
    """
    elements = query_overpass(query)
    data = extract_coords(elements)
    for d in data:
        d["category"] = "commercial_zone"
        d["safety_type"] = "positive"
    return data


def harvest_water_bodies() -> list:
    print("\n[5/7] Fetching water bodies (lakes/rivers)...")
    query = f"""
    [out:json][timeout:60];
    (
      way["natural"="water"]({HYD_BBOX});
      relation["natural"="water"]({HYD_BBOX});
      way["waterway"="river"]({HYD_BBOX});
    );
    out center tags;
    """
    elements = query_overpass(query)
    data = extract_coords(elements)
    for d in data:
        d["category"] = "water_body"
        d["safety_type"] = "negative"
    return data


def harvest_industrial_zones() -> list:
    print("\n[6/7] Fetching industrial zones...")
    query = f"""
    [out:json][timeout:60];
    (
      way["landuse"="industrial"]({HYD_BBOX});
    );
    out center tags;
    """
    elements = query_overpass(query)
    data = extract_coords(elements)
    for d in data:
        d["category"] = "industrial_zone"
        d["safety_type"] = "negative"
    return data


def harvest_road_categories() -> dict:
    print("\n[7/7] Fetching road category summary...")
    query = f"""
    [out:json][timeout:60];
    (
      way["highway"="primary"]({HYD_BBOX});
      way["highway"="secondary"]({HYD_BBOX});
      way["highway"="residential"]({HYD_BBOX});
      way["highway"="unclassified"]({HYD_BBOX});
    );
    out count;
    """
    try:
        resp = requests.post(
            OVERPASS_URL,
            data={"data": query},
            timeout=60,
            headers={"User-Agent": "SafeGirl-Hyderabad-Harvester/1.0"}
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"  Road summary failed: {e}")
        return {}


def save_json(data, filepath: str):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  → Saved: {filepath} ({len(data) if isinstance(data, list) else 'object'})")


def main():
    parser = argparse.ArgumentParser(description="Harvest OSM safety data for Hyderabad")
    parser.add_argument("--output-dir", default="backend/data", help="Output directory")
    args = parser.parse_args()

    print("=" * 60)
    print("SafeGirl — OSM Hyderabad Data Harvester")
    print(f"Bounding box: {HYD_BBOX}")
    print(f"Output: {args.output_dir}")
    print("=" * 60)

    # Harvest all data
    police = harvest_police_stations()
    time.sleep(2)

    metro = harvest_metro_stations()
    time.sleep(2)

    hospitals = harvest_hospitals()
    time.sleep(2)

    commercial = harvest_commercial_zones()
    time.sleep(2)

    water = harvest_water_bodies()
    time.sleep(2)

    industrial = harvest_industrial_zones()
    time.sleep(2)

    roads = harvest_road_categories()

    # Combine and save
    positive_anchors = police + metro + hospitals + commercial
    negative_anchors = water + industrial

    metadata = {
        "_meta": {
            "harvested_at": datetime.utcnow().isoformat() + "Z",
            "bbox": HYD_BBOX,
            "source": "OpenStreetMap Overpass API",
            "total_positive": len(positive_anchors),
            "total_negative": len(negative_anchors),
            "breakdown": {
                "police_stations": len(police),
                "metro_stations": len(metro),
                "hospitals": len(hospitals),
                "commercial_zones": len(commercial),
                "water_bodies": len(water),
                "industrial_zones": len(industrial),
            }
        }
    }

    output_dir = os.path.abspath(args.output_dir)

    save_json({**metadata, "anchors": positive_anchors},
              os.path.join(output_dir, "osm_positive_anchors.json"))

    save_json({**metadata, "anchors": negative_anchors},
              os.path.join(output_dir, "osm_negative_anchors.json"))

    save_json(roads,
              os.path.join(output_dir, "osm_roads_summary.json"))

    print("\n" + "=" * 60)
    print(f"✅ Harvest complete!")
    print(f"   Positive anchors: {len(positive_anchors)}")
    print(f"   Negative anchors: {len(negative_anchors)}")
    print(f"   Files saved to: {output_dir}")
    print("=" * 60)


if __name__ == "__main__":
    main()
