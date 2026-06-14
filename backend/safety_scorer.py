"""
safety_scorer.py
----------------
Computes per-zone safety scores using:
1. The seeded zonal risk database (safety_seed.json)
2. Live OSM Overpass API data for positive/negative anchors
3. Mock SafetiPin audit scores (safetipin_mock.json)

The scorer returns a composite risk score [0.0–1.0] for any given
latitude/longitude point in Hyderabad using spatial interpolation.
"""

import json
import math
import os
import logging
from typing import Optional
import requests

logger = logging.getLogger(__name__)

# ─── Configuration ────────────────────────────────────────────────────────────

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

# Weights for composite score calculation
W_CRIME = 0.40        # Crime index weight
W_LIGHTING = 0.25     # Lighting score weight (inverted)
W_CROWD = 0.15        # Crowd score (inverted for risk — isolated = riskier)
W_OPENNESS = 0.10     # Openness weight (inverted)
W_SAFETIPIN = 0.10    # SafetiPin composite weight (inverted)

# Anchor influence parameters
POSITIVE_ANCHOR_RADIUS_M = 600   # meters — positive anchors reduce risk
NEGATIVE_ANCHOR_RADIUS_M = 400   # meters — negative anchors increase risk
POSITIVE_BONUS = 0.15            # risk reduction near positive anchors
NEGATIVE_PENALTY = 0.20          # risk increase near negative anchors


# ─── Utilities ────────────────────────────────────────────────────────────────

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Returns distance in meters between two GPS points."""
    R = 6371000  # Earth radius in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def load_json(filename: str) -> dict:
    path = os.path.join(DATA_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ─── OSM Overpass Integration ─────────────────────────────────────────────────

class OSMOverpassFetcher:
    """
    Fetches safety-relevant infrastructure from OpenStreetMap Overpass API.
    Results are cached in memory after first fetch.
    """

    def __init__(self):
        self._positive_anchors: Optional[list] = None
        self._negative_anchors: Optional[list] = None

    def _query_overpass(self, query: str) -> list:
        """Execute an Overpass QL query and return list of (lat, lon) tuples."""
        try:
            response = requests.post(
                OVERPASS_URL,
                data={"data": query},
                timeout=30,
                headers={"User-Agent": "SafeGirl-Hyderabad/1.0"}
            )
            response.raise_for_status()
            elements = response.json().get("elements", [])
            coords = []
            for el in elements:
                if "lat" in el and "lon" in el:
                    coords.append((el["lat"], el["lon"]))
                elif "center" in el:
                    coords.append((el["center"]["lat"], el["center"]["lon"]))
            return coords
        except Exception as e:
            logger.warning(f"Overpass API query failed: {e}. Using fallback data.")
            return []

    def get_positive_anchors(self) -> list:
        """
        Positive anchors: Police stations, metro stations, She Teams,
        hospitals, fire stations, and active commercial zones in Hyderabad.
        """
        if self._positive_anchors is not None:
            return self._positive_anchors

        query = """
        [out:json][timeout:30];
        area["name"="Hyderabad"]["admin_level"="6"]->.hyd;
        (
          node["amenity"="police"](area.hyd);
          node["railway"="station"](area.hyd);
          node["station"="subway"](area.hyd);
          node["amenity"="hospital"](area.hyd);
          node["amenity"="fire_station"](area.hyd);
          node["shop"="mall"](area.hyd);
          way["amenity"="police"](area.hyd);
          way["station"="subway"](area.hyd);
        );
        out center;
        """

        anchors = self._query_overpass(query)

        # Fallback hardcoded anchors if Overpass fails
        if not anchors:
            anchors = self._get_fallback_positive_anchors()

        logger.info(f"Loaded {len(anchors)} positive safety anchors from OSM")
        self._positive_anchors = anchors
        return anchors

    def get_negative_anchors(self) -> list:
        """
        Negative anchors: Lake/water body perimeters, isolated transit spots,
        poorly connected nodes derived from OSM.
        """
        if self._negative_anchors is not None:
            return self._negative_anchors

        query = """
        [out:json][timeout:30];
        area["name"="Hyderabad"]["admin_level"="6"]->.hyd;
        (
          node["natural"="water"](area.hyd);
          way["natural"="water"](area.hyd);
          way["waterway"="river"](area.hyd);
          way["landuse"="industrial"](area.hyd);
        );
        out center;
        """

        anchors = self._query_overpass(query)

        if not anchors:
            anchors = self._get_fallback_negative_anchors()

        logger.info(f"Loaded {len(anchors)} negative risk anchors from OSM")
        self._negative_anchors = anchors
        return anchors

    def _get_fallback_positive_anchors(self) -> list:
        """Hardcoded key positive anchors when Overpass is unavailable."""
        return [
            # Police stations (HCP, Cyberabad, Rachakonda major PSs)
            (17.4486, 78.3908),  # Madhapur PS
            (17.4401, 78.3489),  # Gachibowli PS
            (17.4156, 78.4347),  # Banjara Hills PS
            (17.4446, 78.4621),  # Begumpet PS
            (17.3616, 78.4747),  # Charminar PS
            (17.4849, 78.3996),  # Kukatpally PS
            (17.4399, 78.4983),  # Secunderabad PS
            (17.3688, 78.5256),  # Dilsukhnagar PS
            # Metro stations (HMRL Blue + Red Line)
            (17.4505, 78.3801),  # Hitech City Metro
            (17.4946, 78.3587),  # Miyapur Metro
            (17.4375, 78.4483),  # Ameerpet Metro
            (17.3489, 78.5481),  # LB Nagar Metro
            (17.4319, 78.4095),  # Jubilee Hills Check Post Metro
            (17.3852, 78.4638),  # Nampally Metro
            (17.4399, 78.4983),  # Secunderabad Metro
            # She Teams (active beats)
            (17.4500, 78.3850),  # Madhapur She Team
            (17.4150, 78.4300),  # Banjara Hills She Team
            # Major hospitals
            (17.4324, 78.4516),  # Apollo Hospital Jubilee Hills
            (17.3756, 78.4693),  # Osmania General Hospital
        ]

    def _get_fallback_negative_anchors(self) -> list:
        """Hardcoded key negative anchors when Overpass is unavailable."""
        return [
            # Lake perimeters (isolated at night)
            (17.4239, 78.4738),  # Hussain Sagar Center
            (17.3894, 78.4156),  # Mir Alam Tank
            (17.4625, 78.3490),  # Durgam Cheruvu
            (17.4056, 78.4208),  # Tolichowki Lake
            # Musi River belt
            (17.3600, 78.4800),  # Musi Chaderghat
            (17.3622, 78.4937),  # Musi Old Bridge
            (17.3419, 78.5183),  # Saidabad Musi
            # Industrial / isolated zones
            (17.5317, 78.2644),  # Patancheru Industrial
            (17.4812, 78.4419),  # Balanagar Industrial
            (17.4057, 78.5593),  # Uppal Industrial
        ]


# ─── Main Scorer Class ────────────────────────────────────────────────────────

class SafetyScorer:
    """
    Computes a normalized risk score [0.0–1.0] for any GPS point in Hyderabad.

    Score components:
    - Zonal crime index (weighted 0.40)
    - Inverted lighting score (weighted 0.25)
    - Inverted crowd/openness scores (weighted 0.25)
    - SafetiPin composite audit (weighted 0.10)
    - Positive anchor proximity bonus (reduces score)
    - Negative anchor proximity penalty (increases score)
    """

    def __init__(self):
        self.seed_data = load_json("safety_seed.json")
        self.safetipin_data = load_json("safetipin_mock.json")
        self.overpass = OSMOverpassFetcher()
        self._zones = self.seed_data["zones"]
        self._audit_points = self.safetipin_data["audit_points"]
        logger.info(f"SafetyScorer initialized: {len(self._zones)} zones, "
                    f"{len(self._audit_points)} audit points")

    def _get_zone_score(self, lat: float, lon: float) -> float:
        """
        Returns interpolated zonal risk score for a point.
        Uses inverse-distance weighted average of nearby zones.
        """
        weights = []
        scores = []

        for zone in self._zones:
            dist = haversine_distance(lat, lon, zone["lat"], zone["lon"])
            # Only consider zones within 2x their defined radius
            effective_radius_m = zone["radius_km"] * 1000 * 2
            if dist < effective_radius_m:
                # Inverse distance weight
                w = 1.0 / max(dist, 1.0)
                # Composite zone risk score (crime, inverted lighting & crowd)
                risk = (
                    W_CRIME * zone["crime_index"] +
                    W_LIGHTING * (1.0 - zone["lighting_score"]) +
                    W_CROWD * (1.0 - zone["crowd_score"]) +
                    W_OPENNESS * (1.0 - zone["openness"])
                )
                weights.append(w)
                scores.append(risk)

        if not weights:
            # Default medium-risk if no zone matched
            return 0.45

        total_w = sum(weights)
        return sum(w * s for w, s in zip(weights, scores)) / total_w

    def _get_safetipin_score(self, lat: float, lon: float) -> float:
        """
        Returns inverted SafetiPin score (high = risky) for nearest audit point.
        Returns None if no audit point is within 2km.
        """
        best_dist = float("inf")
        best_score = None

        for point in self._audit_points:
            dist = haversine_distance(lat, lon, point["lat"], point["lon"])
            if dist < 2000 and dist < best_dist:
                best_dist = dist
                # composite_score is 0-1 where 1=safe, so invert for risk
                best_score = 1.0 - point["composite_score"]

        return best_score

    def _get_anchor_adjustment(self, lat: float, lon: float) -> float:
        """
        Returns a risk adjustment [-1, 1] based on proximity to safety anchors.
        Negative = safer (near police/metro), Positive = riskier (near lake/industrial)
        """
        adjustment = 0.0

        positive_anchors = self.overpass.get_positive_anchors()
        for anchor_lat, anchor_lon in positive_anchors:
            dist = haversine_distance(lat, lon, anchor_lat, anchor_lon)
            if dist < POSITIVE_ANCHOR_RADIUS_M:
                # Stronger reduction closer to anchor
                factor = 1.0 - (dist / POSITIVE_ANCHOR_RADIUS_M)
                adjustment -= POSITIVE_BONUS * factor

        negative_anchors = self.overpass.get_negative_anchors()
        for anchor_lat, anchor_lon in negative_anchors:
            dist = haversine_distance(lat, lon, anchor_lat, anchor_lon)
            if dist < NEGATIVE_ANCHOR_RADIUS_M:
                factor = 1.0 - (dist / NEGATIVE_ANCHOR_RADIUS_M)
                adjustment += NEGATIVE_PENALTY * factor

        return adjustment

    def get_risk_score(self, lat: float, lon: float) -> float:
        """
        Returns final clamped risk score [0.0–1.0] for a given GPS coordinate.

        0.0 = Completely safe
        1.0 = Extremely high risk
        """
        zone_score = self._get_zone_score(lat, lon)

        safetipin_score = self._get_safetipin_score(lat, lon)
        if safetipin_score is not None:
            # Blend zone score with safetipin
            blended = (
                (1 - W_SAFETIPIN) * zone_score +
                W_SAFETIPIN * safetipin_score
            )
        else:
            blended = zone_score

        anchor_adj = self._get_anchor_adjustment(lat, lon)
        final = blended + anchor_adj

        return max(0.0, min(1.0, final))
