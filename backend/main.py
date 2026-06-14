"""
main.py  –  SafeGirl FastAPI Backend (Telangana State-wide)
------------------------------------------------------
Scalable safety routing engine powered by SQLite and cKDTree spatial index.
"""

import os
import sys
import math
import json
import logging
import time
import sqlite3
import requests
import scipy.spatial
import uvicorn
import osmnx as ox
import networkx as nx
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

# ── Logging ─────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("safegirl")

# ── Paths ────────────────────────────────────────────────────────────────────────
BASE        = os.path.dirname(os.path.abspath(__file__))
DATA_DIR    = os.path.join(BASE, "data")
CACHE_FILE  = os.path.join(DATA_DIR, "hyderabad_drive.graphml")
DB_FILE     = os.path.join(DATA_DIR, "crime_records.db")
FRONTEND    = os.path.abspath(os.path.join(BASE, "..", "frontend"))
os.makedirs(DATA_DIR, exist_ok=True)

# ── Globals ──────────────────────────────────────────────────────────────────────
G              = None   # directed Hyderabad road graph
G_und          = None   # undirected Hyderabad road graph
engine_ok      = False

# Spatial indices & data stores
crime_tree          = None
crime_hotspots_list = []
pos_tree            = None
neg_tree            = None
facility_tree       = None
facilities_list     = []
local_places_index  = []  # cached strings for fuzzy suggestions
lighting_tree       = None
lighting_list       = []

# ── Helpers for Fuzzy Search & Typo Tolerance ────────────────────────────────────
def levenshtein_distance_py(s1: str, s2: str) -> int:
    if len(s1) > len(s2):
        s1, s2 = s2, s1
    distances = range(len(s1) + 1)
    for i2, c2 in enumerate(s2):
        distances_ = [i2+1]
        for i1, c1 in enumerate(s1):
            if c1 == c2:
                distances_.append(distances[i1])
            else:
                distances_.append(1 + min((distances[i1], distances[i1 + 1], distances_[-1])))
        distances = distances_
    return distances[-1]

# ── Database & Spatial Loaders ───────────────────────────────────────────────────
def load_database_records():
    global crime_tree, crime_hotspots_list, pos_tree, neg_tree, facility_tree, facilities_list, local_places_index, lighting_tree, lighting_list
    if not os.path.exists(DB_FILE):
        log.warning("SQLite database not found! Running init_db automatically...")
        try:
            from init_db import init_db
            init_db()
        except Exception as e:
            log.error(f"Failed to auto-initialize SQLite database: {e}")
            raise RuntimeError(f"Database missing and auto-init failed: {e}")

    log.info(f"Loading safety records from SQLite: {DB_FILE}")
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. Load Crime Hotspots
    cursor.execute("SELECT location_name, crime_category, severity, risk_score, latitude, longitude, timestamp, source, confidence_score, verified FROM crime_hotspots")
    crime_rows = cursor.fetchall()
    crime_hotspots_list = []
    crime_coords = []
    neg_coords = []

    for r in crime_rows:
        h = {
            "name": r["location_name"],
            "crime_category": r["crime_category"],
            "severity": r["severity"],
            "risk_score": r["risk_score"],
            "lat": r["latitude"],
            "lng": r["longitude"],
            "timestamp": r["timestamp"],
            "source": r["source"],
            "confidence_score": r["confidence_score"] if "confidence_score" in r.keys() else 0.8,
            "verified": r["verified"] if "verified" in r.keys() else 1
        }
        crime_hotspots_list.append(h)
        crime_coords.append([r["latitude"], r["longitude"]])
        # Negative infrastructure includes unlit spots or high severity crimes
        if r["crime_category"] == "Unlit isolated corridor report" or r["severity"] == "High":
            neg_coords.append([r["latitude"], r["longitude"]])

    if crime_coords:
        crime_tree = scipy.spatial.cKDTree(crime_coords)
    if neg_coords:
        neg_tree = scipy.spatial.cKDTree(neg_coords)

    # 2. Load Public Facilities
    cursor.execute("SELECT name, type, latitude, longitude, details FROM public_facilities")
    fac_rows = cursor.fetchall()
    facilities_list = []
    fac_coords = []
    pos_coords = []

    for r in fac_rows:
        f = {
            "name": r["name"],
            "type": r["type"],
            "lat": r["latitude"],
            "lng": r["longitude"],
            "details": r["details"]
        }
        facilities_list.append(f)
        fac_coords.append([r["latitude"], r["longitude"]])
        if r["type"] in ["police", "transit", "hospital", "clinic", "emergency_shelter", "fire_station"]:
            pos_coords.append([r["latitude"], r["longitude"]])

    if fac_coords:
        facility_tree = scipy.spatial.cKDTree(fac_coords)
    if pos_coords:
        pos_tree = scipy.spatial.cKDTree(pos_coords)

    conn.close()

    # 3. Load SafetiPin Lighting Audits
    sp_path = os.path.join(DATA_DIR, "safetipin_mock.json")
    lighting_list = []
    lighting_coords = []
    if os.path.exists(sp_path):
        try:
            with open(sp_path) as f:
                raw = json.load(f)
            points = raw if isinstance(raw, list) else raw.get("audit_points", [])
            for p in points:
                score = p.get("safety_score") or round(p.get("composite_score", 0.5) * 10, 1)
                lng   = p.get("lng") or p.get("lon")
                if p.get("lat") and lng:
                    lat_val = float(p["lat"])
                    lng_val = float(lng)
                    lighting_list.append({"lat": lat_val, "lng": lng_val, "score": float(score)})
                    lighting_coords.append([lat_val, lng_val])
            if lighting_coords:
                lighting_tree = scipy.spatial.cKDTree(lighting_coords)
            log.info(f"Loaded {len(lighting_list)} SafetiPin lighting audits into cKDTree.")
        except Exception as e:
            log.error(f"Failed to load safetipin_mock.json: {e}")

    # Compile a local fuzzy index for instant autocomplete suggestions
    seen = set()
    local_places_index = []
    
    TELANGANA_LANDMARKS = [
        {"name": "Bus Depot Madhapur", "lat": 17.4475, "lng": 78.3900, "type": "transit"},
        {"name": "Bus Stop Hitech City", "lat": 17.4438, "lng": 78.3780, "type": "transit"},
        {"name": "Bus Station Kukatpally", "lat": 17.4840, "lng": 78.4000, "type": "transit"},
        {"name": "Madhapur Metro Station", "lat": 17.4486, "lng": 78.3910, "type": "transit"},
        {"name": "Madhapur", "lat": 17.4483, "lng": 78.3915, "type": "place"},
        {"name": "Hitech City Metro Station", "lat": 17.4440, "lng": 78.3789, "type": "transit"},
        {"name": "Hitech City", "lat": 17.4435, "lng": 78.3772, "type": "place"},
        {"name": "Gachibowli", "lat": 17.4401, "lng": 78.3489, "type": "place"},
        {"name": "Gachibowli Stadium", "lat": 17.4452, "lng": 78.3440, "type": "transit"},
        {"name": "Kukatpally", "lat": 17.4849, "lng": 78.4011, "type": "place"},
        {"name": "Kukatpally Metro Station", "lat": 17.4855, "lng": 78.4015, "type": "transit"},
        {"name": "Ameerpet", "lat": 17.4375, "lng": 78.4482, "type": "place"},
        {"name": "Ameerpet Metro Station", "lat": 17.4380, "lng": 78.4488, "type": "transit"},
        {"name": "Secunderabad", "lat": 17.4399, "lng": 78.4983, "type": "place"},
        {"name": "Charminar", "lat": 17.3616, "lng": 78.4747, "type": "landmark"},
        {"name": "Begumpet", "lat": 17.4408, "lng": 78.4611, "type": "place"},
        {"name": "Jubilee Hills", "lat": 17.4290, "lng": 78.4075, "type": "place"},
        {"name": "Banjara Hills", "lat": 17.4170, "lng": 78.4430, "type": "place"},
        {"name": "Inorbit Mall Madhapur", "lat": 17.4347, "lng": 78.3830, "type": "mall"},
        {"name": "IKEA Hyderabad", "lat": 17.4366, "lng": 78.3765, "type": "store"},
        {"name": "NIFT Madhapur", "lat": 17.4457, "lng": 78.3887, "type": "college"},
        {"name": "JNTU Kukatpally", "lat": 17.4938, "lng": 78.3916, "type": "university"},
        {"name": "Hitech City Police Station", "lat": 17.4429, "lng": 78.3768, "type": "police"},
        {"name": "Cyber Towers Hitech City", "lat": 17.4442, "lng": 78.3758, "type": "office"},
        
        # Warangal Seeds
        {"name": "Warangal", "lat": 17.9784, "lng": 79.5941, "type": "place"},
        {"name": "Warangal Railway Station", "lat": 17.9650, "lng": 79.6050, "type": "transit"},
        {"name": "Warangal Bus Station", "lat": 17.9700, "lng": 79.6010, "type": "transit"},
        
        # Karimnagar Seeds
        {"name": "Karimnagar", "lat": 18.4386, "lng": 79.1288, "type": "place"},
        {"name": "Karimnagar Bus Stand", "lat": 18.4350, "lng": 79.1200, "type": "transit"},
        {"name": "Karimnagar Collectorate", "lat": 18.4410, "lng": 79.1250, "type": "government_office"},
        
        # Nizamabad Seeds
        {"name": "Nizamabad", "lat": 18.6725, "lng": 78.0941, "type": "place"},
        {"name": "Nizamabad Railway Station", "lat": 18.6750, "lng": 78.1000, "type": "transit"},
        {"name": "Nizamabad Bus Depot", "lat": 18.6700, "lng": 78.0900, "type": "transit"},
        
        # Khammam Seeds
        {"name": "Khammam", "lat": 17.2473, "lng": 80.1514, "type": "place"},
        {"name": "Khammam Bus Station", "lat": 17.2500, "lng": 80.1450, "type": "transit"},
        {"name": "Khammam Railway Station", "lat": 17.2480, "lng": 80.1550, "type": "transit"},
        
        # Adilabad Seeds
        {"name": "Adilabad", "lat": 19.6641, "lng": 78.5320, "type": "place"},
        {"name": "Adilabad Bus Station", "lat": 19.6600, "lng": 78.5280, "type": "transit"},
        {"name": "Adilabad Railway Station", "lat": 19.6670, "lng": 78.5350, "type": "transit"},
        
        # Siddipet Seeds
        {"name": "Siddipet", "lat": 18.1018, "lng": 78.8520, "type": "place"},
        {"name": "Siddipet Bus Station", "lat": 18.1000, "lng": 78.8480, "type": "transit"},
        
        # Mahabubnagar Seeds
        {"name": "Mahabubnagar", "lat": 16.7367, "lng": 77.9889, "type": "place"},
        {"name": "Mahabubnagar Bus Station", "lat": 16.7320, "lng": 77.9850, "type": "transit"},
        
        # Additional Hyderabad/Telangana Core Locations
        {"name": "Mettuguda", "lat": 17.4326, "lng": 78.5204, "type": "place"},
        {"name": "Mettuguda Metro Station", "lat": 17.4323, "lng": 78.5198, "type": "transit"},
        {"name": "Nacharam", "lat": 17.4332, "lng": 78.5587, "type": "place"},
        {"name": "Falaknuma", "lat": 17.32595, "lng": 78.46423, "type": "place"},
        {"name": "Falaknuma Palace", "lat": 17.3308, "lng": 78.4674, "type": "landmark"},
        {"name": "Chandrayangutta", "lat": 17.3117, "lng": 78.4727, "type": "place"},
        {"name": "Tarnaka", "lat": 17.4286, "lng": 78.5378, "type": "place"},
        {"name": "Tarnaka Metro Station", "lat": 17.4283, "lng": 78.5372, "type": "transit"},
        {"name": "Uppal", "lat": 17.4018, "lng": 78.5602, "type": "place"},
        {"name": "Uppal Metro Station", "lat": 17.4014, "lng": 78.5598, "type": "transit"},
        {"name": "Habsiguda", "lat": 17.4167, "lng": 78.5500, "type": "place"},
        {"name": "Dilsukhnagar", "lat": 17.3688, "lng": 78.5247, "type": "place"},
        {"name": "Dilsukhnagar Bus Stand", "lat": 17.3692, "lng": 78.5242, "type": "transit"},
        {"name": "L.B. Nagar", "lat": 17.3457, "lng": 78.5492, "type": "place"},
        {"name": "Mehdipatnam", "lat": 17.3958, "lng": 78.4312, "type": "place"},
        {"name": "Mehdipatnam Bus Depot", "lat": 17.3962, "lng": 78.4308, "type": "transit"},
        {"name": "Koti", "lat": 17.3822, "lng": 78.4819, "type": "place"},
        {"name": "Abids", "lat": 17.3903, "lng": 78.4735, "type": "place"},
        {"name": "Nampally", "lat": 17.3888, "lng": 78.4681, "type": "place"},
        {"name": "Nampally Railway Station", "lat": 17.3892, "lng": 78.4678, "type": "transit"},
        {"name": "MGBS Bus Station", "lat": 17.3783, "lng": 78.4812, "type": "transit"},
        {"name": "JBS Bus Station", "lat": 17.4431, "lng": 78.5005, "type": "transit"},
        {"name": "Secunderabad Railway Station", "lat": 17.4347, "lng": 78.5016, "type": "transit"},
        {"name": "Osmania University", "lat": 17.4137, "lng": 78.5283, "type": "college"},
        {"name": "Kondapur", "lat": 17.4622, "lng": 78.3568, "type": "place"},
        {"name": "Miyapur", "lat": 17.4933, "lng": 78.3512, "type": "place"},
        {"name": "Miyapur Metro Station", "lat": 17.4930, "lng": 78.3508, "type": "transit"}
    ]
    for lm in TELANGANA_LANDMARKS:
        seen.add(lm["name"])
        local_places_index.append(lm)
    
    district_coords = {
        "Adilabad": (19.6641, 78.5320),
        "Bhadradri Kothagudem": (17.5500, 80.6300),
        "Hanamkonda": (18.0100, 79.5800),
        "Hyderabad": (17.3850, 78.4867),
        "Jagtial": (18.8000, 78.9300),
        "Jangaon": (17.7200, 79.1800),
        "Jayashankar Bhupalpally": (18.4300, 79.8600),
        "Jogulamba Gadwal": (16.2300, 77.8000),
        "Kamareddy": (18.3200, 78.3400),
        "Karimnagar": (18.4386, 79.1288),
        "Khammam": (17.2473, 80.1514),
        "Kumuram Bheem Asifabad": (19.3600, 79.2900),
        "Mahabubabad": (17.6100, 80.0100),
        "Mahabubnagar": (16.7367, 77.9889),
        "Mancherial": (18.8700, 79.4300),
        "Medak": (18.0300, 78.2600),
        "Medchal-Malkajgiri": (17.5400, 78.5700),
        "Mulugu": (18.1900, 79.9400),
        "Nagarkurnool": (16.4800, 78.3300),
        "Nalgonda": (17.0500, 79.2700),
        "Narayanpet": (16.7300, 77.5000),
        "Nirmal": (19.1000, 78.3500),
        "Nizamabad": (18.6725, 78.0941),
        "Peddapalli": (18.6100, 79.3800),
        "Rajanna Sircilla": (18.3900, 78.8300),
        "Rangareddy": (17.2000, 78.3500),
        "Sangareddy": (17.6100, 78.0800),
        "Siddipet": (18.1018, 78.8520),
        "Suryapet": (17.1500, 79.6200),
        "Vikarabad": (17.3300, 77.9000),
        "Wanaparthy": (16.3600, 78.0600),
        "Warangal": (17.9784, 79.5941),
        "Yadadri Bhuvanagiri": (17.5100, 78.8800),
        "Bhongir": (17.5100, 78.8800),
        "Secunderabad": (17.4399, 78.4983)
    }
    for d, coords in district_coords.items():
        name = f"{d} District"
        if name not in seen:
            seen.add(name)
            local_places_index.append({"name": name, "lat": coords[0], "lng": coords[1], "type": "administrative"})
        if d not in seen:
            seen.add(d)
            local_places_index.append({"name": d, "lat": coords[0], "lng": coords[1], "type": "place"})
    
    for c in crime_hotspots_list:
        n = c["name"].split("#")[0].strip()
        if n not in seen:
            seen.add(n)
            local_places_index.append({"name": n, "lat": c["lat"], "lng": c["lng"], "type": "crime_spot"})

    for f in facilities_list:
        n = f["name"].split("#")[0].strip()
        if n not in seen:
            seen.add(n)
            local_places_index.append({"name": n, "lat": f["lat"], "lng": f["lng"], "type": f["type"]})

    log.info(f"Loaded {len(crime_hotspots_list)} crime records and {len(facilities_list)} public facilities into cKDTrees.")
    log.info(f"Local suggestions index ready with {len(local_places_index)} anchors.")


# ── Safety & Scoring Engine Helpers ──────────────────────────────────────────────
def calculate_localized_crime_score(lat: float, lng: float) -> float:
    if crime_tree is None or not crime_hotspots_list:
        return 1.5
    
    indices = crime_tree.query_ball_point([lat, lng], r=500.0/111000.0)
    if not indices:
        return 1.0
    
    total_risk = 0.0
    for idx in indices:
        hotspot = crime_hotspots_list[idx]
        h_lat, h_lng = hotspot["lat"], hotspot["lng"]
        dist_m = math.sqrt((lat - h_lat)**2 + (lng - h_lng)**2) * 111000.0
        decay = max(0.0, 1.0 - dist_m / 500.0)
        
        severity_mult = 1.0
        if hotspot["severity"] == "High":
            severity_mult = 1.8
        elif hotspot["severity"] == "Medium":
            severity_mult = 1.3
            
        total_risk += hotspot["risk_score"] * decay * severity_mult
        
    return round(min(10.0, 1.0 + total_risk), 2)


def calculate_localized_infra_risk(lat: float, lng: float) -> float:
    base_risk = 6.0
    
    lighting_adj = 0.0
    if lighting_tree is not None and lighting_list:
        dist_l, idx_l = lighting_tree.query([lat, lng], k=1)
        dist_l_m = dist_l * 111000.0
        if dist_l_m <= 400.0:
            audit = lighting_list[idx_l]
            lighting_adj = (5.0 - audit["score"]) * 0.4
            decay = max(0.0, 1.0 - dist_l_m / 400.0)
            lighting_adj *= decay

    police_benefit = 0.0
    haven_benefit = 0.0
    asset_benefit = 0.0
    
    if facility_tree is not None and facilities_list:
        indices = facility_tree.query_ball_point([lat, lng], r=800.0/111000.0)
        for idx in indices:
            fac = facilities_list[idx]
            f_lat, f_lng = fac["lat"], fac["lng"]
            dist_m = math.sqrt((lat - f_lat)**2 + (lng - f_lng)**2) * 111000.0
            
            if fac["type"] == "police":
                if dist_m <= 600.0:
                    police_benefit = max(police_benefit, 3.0 * (1.0 - dist_m / 600.0))
            elif fac["type"] in ["hospital", "clinic", "emergency_shelter", "fire_station"]:
                if dist_m <= 600.0:
                    haven_benefit = max(haven_benefit, 2.0 * (1.0 - dist_m / 600.0))
            elif fac["type"] in ["transit", "pharmacy", "petrol_pump", "government_office"]:
                if dist_m <= 400.0:
                    asset_benefit = max(asset_benefit, 1.0 * (1.0 - dist_m / 400.0))
                    
    final_risk = base_risk + lighting_adj - police_benefit - haven_benefit - asset_benefit
    return round(max(0.5, min(10.0, final_risk)), 2)


def get_crime_score(lat, lng):
    return calculate_localized_crime_score(lat, lng)


def get_infra_risk(lat, lng):
    return calculate_localized_infra_risk(lat, lng)


# ── Load Hyderabad Graph ────────────────────────────────────────────────────────
def load_graph():
    global G, G_und, engine_ok
    ox.settings.log_console = False
    ox.settings.use_cache   = True

    if os.path.exists(CACHE_FILE):
        log.info(f"Loading cached graph: {CACHE_FILE}")
        t = time.time()
        G = ox.load_graphml(CACHE_FILE)
        log.info(f"Graph loaded in {time.time()-t:.1f}s — {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    else:
        log.info("No cache found – downloading Hyderabad road graph from OSM...")
        t = time.time()
        try:
            G = ox.graph_from_place("Hyderabad, Telangana, India", network_type="drive", simplify=True)
        except Exception as e:
            log.warning(f"Place download failed ({e}), falling back to bbox...")
            G = ox.graph_from_bbox(17.55, 17.30, 78.60, 78.30, network_type="drive")
        log.info(f"Downloaded in {time.time()-t:.1f}s — {G.number_of_nodes()} nodes")
        _apply_safety_weights()

    G_und = G.to_undirected()
    engine_ok = True
    log.info("SafeGirl routing engine READY.")


def _apply_safety_weights():
    """Stamp crime_score, infra_risk, speed_kph, and travel_time on every edge."""
    log.info(f"Stamping safety weights and travel times on {G.number_of_edges()} edges using cKDTree...")
    t = time.time()
    for u, v, k, d in G.edges(keys=True, data=True):
        n_u = G.nodes[u]; n_v = G.nodes[v]
        mid_lat = (n_u["y"] + n_v["y"]) / 2
        mid_lng = (n_u["x"] + n_v["x"]) / 2
        
        # Local safety scores
        d["crime_score"] = calculate_localized_crime_score(mid_lat, mid_lng)
        d["infra_risk"]  = calculate_localized_infra_risk(mid_lat, mid_lng)
        
        # speed limit (parse speed safely from maxspeed or fallback to default)
        speed_val = d.get("maxspeed", 35.0)
        if isinstance(speed_val, list):
            speed_val = speed_val[0]
        if isinstance(speed_val, str):
            try:
                speed_val = float("".join(c for c in speed_val if c.isdigit() or c == "."))
            except ValueError:
                speed_val = 35.0
        else:
            try:
                speed_val = float(speed_val)
            except (ValueError, TypeError):
                speed_val = 35.0
                
        d["speed_kph"] = speed_val
        speed_mps = speed_val * 1000.0 / 3600.0
        length = float(d.get("length", 10.0))
        d["travel_time"] = length / max(speed_mps, 1.0)
        
    log.info(f"Stamping completed in {time.time()-t:.1f}s. Saving enriched graph back to cache...")
    ox.save_graphml(G, CACHE_FILE)
    log.info("Cache updated.")


# ── Custom weight function factory ───────────────────────────────────────────────
def make_weight_fn(alpha, beta):
    def weight_fn(u, v, data):
        weights = []
        for edge_data in data.values():
            length = float(edge_data.get("length", 1.0))
            crime  = float(edge_data.get("crime_score", 5.0)) / 10.0
            infra  = float(edge_data.get("infra_risk",  5.0)) / 10.0
            w = length * (1.0 + alpha * crime + beta * infra)
            weights.append(w)
        return min(weights) if weights else 1.0
    return weight_fn


# ── Route metrics calculator ─────────────────────────────────────────────────────
def extract_metrics(path_nodes, graph, speed_mps=9.7):
    coords = []
    total_m = total_c = total_i = total_t = 0.0
    for i, node in enumerate(path_nodes):
        nd = graph.nodes[node]
        coords.append([round(nd["y"], 6), round(nd["x"], 6)])
        if i < len(path_nodes) - 1:
            raw = graph[node][path_nodes[i + 1]]
            ed  = next(iter(raw.values())) if hasattr(raw, "values") else raw
            length = float(ed.get("length",      0.0))
            crime  = float(ed.get("crime_score", 5.0))
            infra  = float(ed.get("infra_risk",  5.0))
            travel_time = float(ed.get("travel_time", length / speed_mps))
            total_m += length
            total_c += crime * length
            total_i += infra * length
            total_t += travel_time

    avg_c = round(total_c / total_m, 2) if total_m else 5.0
    avg_i = round(total_i / total_m, 2) if total_m else 5.0
    safety = round(max(0.0, min(10.0, 10.0 - (avg_c + avg_i) / 2.0)), 2)
    duration_sec = int(total_t) if total_t > 0 else int(total_m / speed_mps)
    return coords, {
        "distance_km":           round(total_m / 1000.0, 2),
        "duration":              duration_sec,
        "avg_crime_score":       avg_c,
        "avg_infra_risk_score":  avg_i,
        "safety_rating":         safety,
        "node_count":            len(path_nodes),
    }


def find_facilities_along_corridor(route_coords, radius_m=300.0) -> list:
    if facility_tree is None or not facilities_list or not route_coords:
        return []
    
    matched_indices = set()
    r_deg = radius_m / 111000.0
    for lat, lng in route_coords:
        indices = facility_tree.query_ball_point([lat, lng], r=r_deg)
        matched_indices.update(indices)
        
    results = []
    for idx in sorted(matched_indices):
        f = facilities_list[idx]
        results.append({
            "name": f["name"],
            "type": f["type"],
            "lat": f["lat"],
            "lng": f["lng"],
            "details": f["details"]
        })
    return results


# ── OSRM Route Scorer ─────────────────────────────────────────────────────────────
def score_osrm_route(coords, alpha, beta, distance_m, duration_sec):
    if not coords or len(coords) < 2:
        return {
            "route": coords,
            "cost_safest": 999999.0,
            "cost_balanced": 999999.0,
            "cost_fastest": 999999.0,
            "metrics": {
                "distance_km": 0.0,
                "duration": 0,
                "avg_crime_score": 5.0,
                "avg_infra_risk_score": 5.0,
                "safety_rating": 5.0,
                "node_count": len(coords)
            }
        }
        
    total_m = distance_m
    total_c = 0.0
    total_i = 0.0
    
    # Sample up to 80 points to speed up spatial queries on long routes
    step = max(1, len(coords) // 80)
    sampled = coords[::step]
    if coords[-1] not in sampled:
        sampled.append(coords[-1])
    
    for lat, lng in sampled:
        crime = calculate_localized_crime_score(lat, lng)
        infra = calculate_localized_infra_risk(lat, lng)
        total_c += crime
        total_i += infra
        
    avg_c = round(total_c / len(sampled), 2) if sampled else 5.0
    avg_i = round(total_i / len(sampled), 2) if sampled else 5.0
    safety = round(max(0.0, min(10.0, 10.0 - (avg_c + avg_i) / 2.0)), 2)
    
    # Cost structures to select Fastest, Safest, Balanced
    cost_safest = total_m * (1.0 + 150.0 * (avg_c / 10.0) + 150.0 * (avg_i / 10.0))
    cost_balanced = total_m * (1.0 + 10.0 * (avg_c / 10.0) + 10.0 * (avg_i / 10.0))
    cost_fastest = duration_sec  # optimized for travel duration

    return {
        "route": coords,
        "cost_safest": cost_safest,
        "cost_balanced": cost_balanced,
        "cost_fastest": cost_fastest,
        "metrics": {
            "distance_km":           round(total_m / 1000.0, 2),
            "duration":              int(duration_sec),
            "avg_crime_score":       avg_c,
            "avg_infra_risk_score":  avg_i,
            "safety_rating":         safety,
            "node_count":            len(coords)
        }
    }


# ── Lifespan event handler ────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize/connect database records
    load_database_records()
    # Load Hyderabad road graph
    load_graph()
    yield

# ── FastAPI app ───────────────────────────────────────────────────────────────────
app = FastAPI(
    title="SafeGirl – Telangana Safety Route Planner", 
    version="4.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware, 
    allow_origins=["*"],
    allow_methods=["*"], 
    allow_headers=["*"]
)


# ── /api/health ───────────────────────────────────────────────────────────────────
@app.get("/api/health")
@app.get("/health")
def health():
    return {
        "status":       "healthy" if engine_ok else "loading",
        "engine_ready": engine_ok,
        "city":         "Hyderabad, Telangana, India",
        "nodes":        G.number_of_nodes() if G else 0,
        "edges":        G.number_of_edges() if G else 0,
        "crime_hotspots_count": len(crime_hotspots_list),
        "facilities_count": len(facilities_list)
    }


# ── /api/autocomplete ─────────────────────────────────────────────────────────────
@app.get("/api/autocomplete")
@app.get("/autocomplete")
def autocomplete(q: str = Query(...)):
    if not q or len(q.strip()) < 1:
        return []
        
    q_clean = q.lower().strip()
    
    # 1. Query Local fuzzy places index
    local_hits = []
    for p in local_places_index:
        name_lower = p["name"].lower()
        score = 0.0
        
        if name_lower == q_clean:
            score = 100.0
        elif name_lower.startswith(q_clean):
            score = 90.0 + (len(q_clean) / len(name_lower)) * 5.0
        else:
            words = name_lower.split()
            word_match = False
            for w in words:
                if w.startswith(q_clean):
                    score = 80.0 + (len(q_clean) / len(name_lower)) * 5.0
                    word_match = True
                    break
            
            if not word_match:
                if q_clean in name_lower:
                    score = 70.0 + (len(q_clean) / len(name_lower)) * 5.0
                else:
                    # Fuzzy / Typo tolerance: Levenshtein distance on words
                    min_dist = 999
                    for w in words:
                        dist = levenshtein_distance_py(q_clean, w)
                        if dist < min_dist:
                            min_dist = dist
                    
                    if len(q_clean) >= 3 and min_dist <= 1:
                        score = 50.0 - min_dist
                    elif len(q_clean) >= 5 and min_dist <= 2:
                        score = 40.0 - min_dist
                        
        if score > 0:
            local_hits.append((p, score))
            
    # Sort by matching score descending
    local_hits.sort(key=lambda x: -x[1])
    
    results = []
    seen = set()
    for item, _ in local_hits[:15]:
        seen.add(item["name"])
        results.append({
            "place_id": hash(item["name"]),
            "display_name": item["name"] + ", Hyderabad, Telangana, India",
            "lat": item["lat"],
            "lon": item["lng"],
            "type": item["type"],
            "class": "local"
        })

    # 2. Query Nominatim to cover general addresses/landmarks
    headers = {
        "User-Agent": "SafeGirlTelanganaSafetyPlanner/1.0 (contact@safegirl.com)",
        "Accept-Language": "en"
    }
    vbox = "77.00,15.80,81.50,19.90"
    url1 = f"https://nominatim.openstreetmap.org/search?q={requests.utils.quote(q_clean + ' Telangana India')}&format=json&limit=10&countrycodes=in&bounded=1&viewbox={vbox}&dedupe=1"
    
    try:
        r1 = requests.get(url1, headers=headers, timeout=4)
        if r1.ok:
            for item in r1.json():
                name = item.get("display_name", "")
                short_name = name.split(",")[0].strip()
                if short_name in seen:
                    continue
                seen.add(short_name)
                
                lat = float(item.get("lat", 0.0))
                lon = float(item.get("lon", 0.0))
                
                # Verify bounds
                if 15.80 <= lat <= 19.90 and 77.00 <= lon <= 81.50:
                    results.append({
                        "place_id": item.get("place_id"),
                        "display_name": name,
                        "lat": lat,
                        "lon": lon,
                        "type": item.get("type") or item.get("class") or "place",
                        "class": "nominatim"
                    })
    except Exception as e:
        log.warning(f"Nominatim lookup failed: {e}")

    # Return up to 15 relevant suggestions
    return results[:15]


# ── /api/crime-zones ──────────────────────────────────────────────────────────────
@app.get("/api/crime-zones")
@app.get("/crime-zones")
def crime_zones(
    bbox: str = Query(None),
    category: str = Query(None),
    severity: str = Query(None),
    min_risk: float = Query(None),
):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    query = "SELECT location_name, crime_category, severity, risk_score, latitude, longitude, timestamp, source, confidence_score, verified FROM crime_hotspots WHERE 1=1"
    params = []
    
    if bbox:
        try:
            lng_min, lat_min, lng_max, lat_max = map(float, bbox.split(","))
            query += " AND latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?"
            params.extend([lat_min, lat_max, lng_min, lng_max])
        except ValueError:
            pass
            
    if category and category != "All" and category != "undefined":
        query += " AND crime_category = ?"
        params.append(category)
        
    if severity and severity != "All" and severity != "undefined":
        query += " AND severity = ?"
        params.append(severity)
        
    if min_risk is not None:
        query += " AND risk_score >= ?"
        params.append(min_risk)
        
    # Cap hotspots query at 4,000 for client rendering performance
    query += " LIMIT 4000"
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    
    zones = []
    for r in rows:
        zones.append({
            "name": r["location_name"],
            "lat": r["latitude"],
            "lng": r["longitude"],
            "crime_category": r["crime_category"],
            "severity": r["severity"],
            "crime_score": r["risk_score"],
            "timestamp": r["timestamp"],
            "source": r["source"],
            "confidence_score": r["confidence_score"] if "confidence_score" in r.keys() else 0.8,
            "verified": r["verified"] if "verified" in r.keys() else 1
        })
    return {"zones": zones}


# ── /api/route ────────────────────────────────────────────────────────────────────
@app.get("/api/route")
@app.get("/route")
def safe_route(
    start_lat: float = Query(...), start_lng: float = Query(...),
    end_lat:   float = Query(...), end_lng:   float = Query(...),
    alpha:     float = Query(5.0, ge=0, le=20),
    beta:      float = Query(5.0, ge=0, le=20),
):
    if not engine_ok:
        raise HTTPException(503, "Routing engine is warming up. Try again in a few seconds.")

    def is_in_hyd(lat, lng):
        return (17.20 <= lat <= 17.65) and (78.20 <= lng <= 78.68)

    use_local = is_in_hyd(start_lat, start_lng) and is_in_hyd(end_lat, end_lng)
    
    if use_local:
        log.info("Computing route using local Hyderabad Dijkstra engine...")
        try:
            src = ox.nearest_nodes(G, start_lng, start_lat)
            dst = ox.nearest_nodes(G, end_lng,   end_lat)

            # Three weight configurations
            wfn_fastest = lambda u, v, d: min(float(edge_data.get("travel_time", 1.0)) for edge_data in d.values())
            wfn_safest = make_weight_fn(150.0, 150.0)
            wfn_balanced = make_weight_fn(10.0, 10.0)

            # 1. Fastest local route
            try:
                p_f = nx.shortest_path(G, src, dst, weight=wfn_fastest)
                use_gf = G
            except nx.NetworkXNoPath:
                p_f = nx.shortest_path(G_und, src, dst, weight=wfn_fastest)
                use_gf = G_und

            # 2. Safest local route
            try:
                p_s = nx.shortest_path(G, src, dst, weight=wfn_safest)
                use_gs = G
            except nx.NetworkXNoPath:
                p_s = nx.shortest_path(G_und, src, dst, weight=wfn_safest)
                use_gs = G_und

            # 3. Balanced local route
            try:
                p_b = nx.shortest_path(G, src, dst, weight=wfn_balanced)
                use_gb = G
            except nx.NetworkXNoPath:
                p_b = nx.shortest_path(G_und, src, dst, weight=wfn_balanced)
                use_gb = G_und

            # Extract metrics & corridors
            coords_f, metrics_f = extract_metrics(p_f, use_gf, speed_mps=9.7) # 35km/h
            coords_s, metrics_s = extract_metrics(p_s, use_gs, speed_mps=8.3) # 30km/h
            coords_b, metrics_b = extract_metrics(p_b, use_gb, speed_mps=9.1) # 33km/h

            # Calculate Travel Score and Balanced Score
            min_duration = min(metrics_f["duration"], metrics_s["duration"], metrics_b["duration"])
            
            for m in [metrics_f, metrics_s, metrics_b]:
                dur = m["duration"]
                m["travel_score"] = round(10.0 * (min_duration / max(1, dur)), 1)
                m["balanced_score"] = round(0.5 * m["safety_rating"] + 0.5 * m["travel_score"], 1)

            # public facilities corridor
            fac_f = find_facilities_along_corridor(coords_f)
            fac_s = find_facilities_along_corridor(coords_s)
            fac_b = find_facilities_along_corridor(coords_b)

            return {
                "status": "success",
                "fastest": { "route": coords_f, "metrics": metrics_f, "facilities": fac_f },
                "safest": { "route": coords_s, "metrics": metrics_s, "facilities": fac_s },
                "balanced": { "route": coords_b, "metrics": metrics_b, "facilities": fac_b }
            }

        except Exception as e:
            log.warning(f"Local routing failed ({e}). Falling back to OSRM alternative scorer...")
            pass

    log.info(f"Computing route using OSRM alternative route scorer for Telangana (Start: {start_lat},{start_lng} -> End: {end_lat},{end_lng})...")
    try:
        # Fetch up to 3 OSRM driving routes
        url = f"https://router.project-osrm.org/route/v1/driving/{start_lng},{start_lat};{end_lng},{end_lat}?overview=full&geometries=geojson&alternatives=true"
        r = requests.get(url, timeout=12)
        if not r.ok:
            raise HTTPException(r.status_code, f"OSRM service error: {r.text}")
        data = r.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            raise HTTPException(404, "No route found by OSRM.")

        routes = data["routes"]
        scored_routes = []
        for route in routes:
            geojson_coords = route["geometry"]["coordinates"]
            coords = [[c[1], c[0]] for c in geojson_coords]
            scored = score_osrm_route(coords, alpha, beta, route["distance"], route["duration"])
            scored_routes.append(scored)

        # Select the three routes (Fastest, Safest, Balanced)
        r_fastest = min(scored_routes, key=lambda x: x["cost_fastest"])
        r_safest = min(scored_routes, key=lambda x: x["cost_safest"])
        r_balanced = min(scored_routes, key=lambda x: x["cost_balanced"])

        # Calculate Travel Score and Balanced Score for OSRM routes
        min_duration = min(r_fastest["metrics"]["duration"], r_safest["metrics"]["duration"], r_balanced["metrics"]["duration"])
        
        for r in [r_fastest, r_safest, r_balanced]:
            dur = r["metrics"]["duration"]
            r["metrics"]["travel_score"] = round(10.0 * (min_duration / max(1, dur)), 1)
            r["metrics"]["balanced_score"] = round(0.5 * r["metrics"]["safety_rating"] + 0.5 * r["metrics"]["travel_score"], 1)

        # Detect corridor facilities
        fac_f = find_facilities_along_corridor(r_fastest["route"])
        fac_s = find_facilities_along_corridor(r_safest["route"])
        fac_b = find_facilities_along_corridor(r_balanced["route"])

        return {
            "status": "success",
            "fastest": { "route": r_fastest["route"], "metrics": r_fastest["metrics"], "facilities": fac_f },
            "safest": { "route": r_safest["route"], "metrics": r_safest["metrics"], "facilities": fac_s },
            "balanced": { "route": r_balanced["route"], "metrics": r_balanced["metrics"], "facilities": fac_b }
        }

    except Exception as e:
        log.error(f"Route error: {e}")
        raise HTTPException(500, f"Routing error: {e}")


# ── /api/infrastructure ───────────────────────────────────────────────────────────
@app.get("/api/infrastructure")
@app.get("/infrastructure")
def infrastructure():
    # Return all public facilities in the database for default rendering
    # (capped to prevent client lag)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT name, type, latitude, longitude, details FROM public_facilities LIMIT 300")
    rows = cursor.fetchall()
    conn.close()
    
    pos = []
    for r in rows:
        pos.append({
            "name": r["name"],
            "type": r["type"],
            "lat": r["latitude"],
            "lng": r["longitude"],
            "details": r["details"]
        })
        
    sp_path = os.path.join(DATA_DIR, "safetipin_mock.json")
    safetipin = []
    if os.path.exists(sp_path):
        with open(sp_path) as f:
            raw = json.load(f)
        points = raw if isinstance(raw, list) else raw.get("audit_points", [])
        for p in points:
            score = p.get("safety_score") or round(p.get("composite_score", 0.5) * 10, 1)
            lng   = p.get("lng") or p.get("lon")
            if p.get("lat") and lng:
                safetipin.append({
                    "lat":          p["lat"],
                    "lng":          float(lng),
                    "safety_score": score,
                    "name":         p.get("name", "Audit Point"),
                })
    return {"positive": pos, "negative": [], "safetipin": safetipin}


# ── Static files ──────────────────────────────────────────────────────────────────
@app.get("/")
@app.get("/index.html")
def serve_index():
    return FileResponse(os.path.join(FRONTEND, "index.html"))

@app.get("/{path:path}")
def serve_file(path: str):
    target = os.path.join(FRONTEND, path)
    if os.path.isfile(target):
        return FileResponse(target)
    return FileResponse(os.path.join(FRONTEND, "index.html"))


# ── Entry ─────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
