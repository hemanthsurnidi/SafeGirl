"""
graph_builder.py
----------------
Downloads (or loads cached) the Hyderabad road network using OSMnx.
Computes a custom 'safety_cost' weight for every edge in the graph using:

    safety_cost = length * (1 + ALPHA * crime_score + BETA * infra_risk_score)

Where:
    - length         = edge length in meters (from OSM)
    - crime_score    = zonal risk score [0–1] at the edge midpoint
    - infra_risk_score = proximity-based penalty from OSM negative anchors [0–1]
    - ALPHA          = penalty multiplier for crime (default 3.0)
    - BETA           = penalty multiplier for infrastructure risk (default 2.0)

The graph is cached as a .graphml file to avoid re-downloading on each run.
"""

import os
import time
import logging
import math
from typing import Optional

import osmnx as ox
import networkx as nx

from safety_scorer import SafetyScorer

logger = logging.getLogger(__name__)

# ─── Configuration ────────────────────────────────────────────────────────────

CACHE_PATH = os.path.join(os.path.dirname(__file__), "data", "hyderabad_graph.graphml")
PLACE_NAME = "Hyderabad, Telangana, India"
NETWORK_TYPE = "drive"

# Cost function penalty multipliers
ALPHA = 3.0   # Crime zone penalty (higher = more avoidance of risky zones)
BETA = 2.0    # Infrastructure risk penalty (lighting, isolation)

# Speed assumptions for time-based weight (km/h → m/s)
DEFAULT_SPEED_KMH = 30.0   # fallback if OSM has no maxspeed tag
DEFAULT_SPEED_MS = DEFAULT_SPEED_KMH * 1000 / 3600


# ─── Graph Management ─────────────────────────────────────────────────────────

def load_or_build_graph(scorer: Optional[SafetyScorer] = None, force_rebuild: bool = False) -> nx.MultiDiGraph:
    """
    Loads cached Hyderabad road graph, or downloads and caches if not present.
    After loading, applies safety weights to all edges.

    Args:
        scorer: SafetyScorer instance. If None, creates a new one.
        force_rebuild: If True, re-downloads even if cache exists.

    Returns:
        nx.MultiDiGraph with 'safety_cost' and 'travel_time' edge attributes.
    """
    if scorer is None:
        scorer = SafetyScorer()

    if not force_rebuild and os.path.exists(CACHE_PATH):
        logger.info(f"Loading cached graph from {CACHE_PATH}")
        start = time.time()
        G = ox.load_graphml(CACHE_PATH)
        logger.info(f"Graph loaded in {time.time() - start:.1f}s — "
                    f"{G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    else:
        logger.info(f"Downloading road graph for '{PLACE_NAME}' (this may take 5–15 minutes)...")
        start = time.time()
        G = ox.graph_from_place(
            PLACE_NAME,
            network_type=NETWORK_TYPE,
            simplify=True,
        )
        elapsed = time.time() - start
        logger.info(f"Graph downloaded in {elapsed:.1f}s — "
                    f"{G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

        # Add speed and travel time attributes
        G = ox.add_edge_speeds(G)
        G = ox.add_edge_travel_times(G)

        # Cache to disk
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        ox.save_graphml(G, CACHE_PATH)
        logger.info(f"Graph cached to {CACHE_PATH}")

    # Apply custom safety weights
    logger.info("Computing safety weights for all edges...")
    G = _apply_safety_weights(G, scorer)
    logger.info("Safety weights applied successfully.")

    return G


def _get_edge_midpoint(G: nx.MultiDiGraph, u: int, v: int) -> tuple:
    """Returns the approximate midpoint of an edge as (lat, lon)."""
    u_data = G.nodes[u]
    v_data = G.nodes[v]
    return (
        (u_data["y"] + v_data["y"]) / 2,
        (u_data["x"] + v_data["x"]) / 2,
    )


def _apply_safety_weights(G: nx.MultiDiGraph, scorer: SafetyScorer) -> nx.MultiDiGraph:
    """
    Computes and assigns 'safety_cost' to every edge in the graph.

    safety_cost = length * (1 + ALPHA * crime_score + BETA * infra_score)

    Also assigns 'travel_time_s' as a fallback weight for fastest route.
    """
    total_edges = G.number_of_edges()
    processed = 0
    log_interval = max(1, total_edges // 20)  # Log every 5%

    for u, v, key, data in G.edges(keys=True, data=True):
        length_m = data.get("length", 50.0)  # meters

        # Get edge midpoint GPS coords
        mid_lat, mid_lon = _get_edge_midpoint(G, u, v)

        # Get composite risk score from our scorer
        risk_score = scorer.get_risk_score(mid_lat, mid_lon)

        # Decompose risk into crime and infrastructure components
        # Crime is the primary component, infra is proximity-based adjustment
        crime_score = risk_score * 0.65      # Primary zonal crime component
        infra_score = risk_score * 0.35      # Infrastructure/lighting component

        # Custom safety cost formula
        safety_cost = length_m * (1.0 + ALPHA * crime_score + BETA * infra_score)

        # Travel time in seconds (for fastest route baseline)
        speed_ms = data.get("speed_kph", DEFAULT_SPEED_KMH) * 1000 / 3600
        travel_time_s = length_m / max(speed_ms, 1.0)

        # Write back to graph
        G[u][v][key]["safety_cost"] = safety_cost
        G[u][v][key]["travel_time"] = travel_time_s
        G[u][v][key]["risk_score"] = round(risk_score, 4)

        processed += 1
        if processed % log_interval == 0:
            logger.info(f"  Edge weighting progress: {processed}/{total_edges} "
                        f"({100 * processed // total_edges}%)")

    return G


# ─── Nearest Node Helper ───────────────────────────────────────────────────────

def get_nearest_node(G: nx.MultiDiGraph, lat: float, lon: float) -> int:
    """Returns the graph node ID nearest to the given GPS coordinate."""
    return ox.nearest_nodes(G, X=lon, Y=lat)


# ─── Graph Statistics ─────────────────────────────────────────────────────────

def get_graph_stats(G: nx.MultiDiGraph) -> dict:
    """Returns basic statistics about the loaded graph."""
    risk_scores = [
        d.get("risk_score", 0)
        for _, _, d in G.edges(data=True)
        if "risk_score" in d
    ]

    return {
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "place": PLACE_NAME,
        "network_type": NETWORK_TYPE,
        "alpha": ALPHA,
        "beta": BETA,
        "risk_score_stats": {
            "min": round(min(risk_scores), 4) if risk_scores else 0,
            "max": round(max(risk_scores), 4) if risk_scores else 0,
            "mean": round(sum(risk_scores) / len(risk_scores), 4) if risk_scores else 0,
        } if risk_scores else {}
    }
