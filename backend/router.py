"""
router.py
---------
Implements the custom safe routing algorithm using NetworkX Dijkstra
on the safety-weighted Hyderabad road graph.

Routes returned:
- Safest Route: nx.shortest_path with weight='safety_cost'
- Uses the same graph for fallback length-based routing if needed

Output format:
    {
        "coordinates": [[lat, lon], ...],
        "distance_m": float,
        "safety_score": float,  // average risk score along route (0=safe, 1=risky)
        "safety_grade": str,    // "A", "B", "C", "D", "F"
        "node_count": int
    }
"""

import logging
import networkx as nx

logger = logging.getLogger(__name__)

# Safety grade thresholds (based on average risk score along route)
GRADE_THRESHOLDS = [
    (0.20, "A"),
    (0.35, "B"),
    (0.50, "C"),
    (0.65, "D"),
    (1.01, "F"),
]


def get_safety_grade(avg_risk: float) -> str:
    """Converts average risk score to letter grade."""
    for threshold, grade in GRADE_THRESHOLDS:
        if avg_risk < threshold:
            return grade
    return "F"


def compute_safest_route(
    G: nx.MultiDiGraph,
    origin_node: int,
    dest_node: int,
) -> dict:
    """
    Computes the safest path using Dijkstra on the 'safety_cost' weight.

    Returns a dict with coordinates, distance, and safety score.
    Raises nx.NetworkXNoPath if no path exists.
    """
    logger.info(f"Computing safest route: {origin_node} → {dest_node}")

    try:
        path_nodes = nx.shortest_path(
            G,
            source=origin_node,
            target=dest_node,
            weight="safety_cost",
            method="dijkstra",
        )
    except nx.NetworkXNoPath:
        logger.error(f"No safe path found between {origin_node} and {dest_node}")
        raise

    return _extract_route_info(G, path_nodes, weight_key="safety_cost")


def compute_fastest_route(
    G: nx.MultiDiGraph,
    origin_node: int,
    dest_node: int,
) -> dict:
    """
    Computes the fastest path using Dijkstra on 'travel_time' weight.
    This is the graph-based baseline; the frontend also fetches from Google Directions.
    """
    logger.info(f"Computing fastest route: {origin_node} → {dest_node}")

    try:
        path_nodes = nx.shortest_path(
            G,
            source=origin_node,
            target=dest_node,
            weight="travel_time",
            method="dijkstra",
        )
    except nx.NetworkXNoPath:
        # Fallback to length-based
        path_nodes = nx.shortest_path(
            G,
            source=origin_node,
            target=dest_node,
            weight="length",
            method="dijkstra",
        )

    return _extract_route_info(G, path_nodes, weight_key="travel_time")


def _extract_route_info(
    G: nx.MultiDiGraph,
    path_nodes: list,
    weight_key: str,
) -> dict:
    """
    Converts a list of node IDs to a route info dict.

    Returns:
        coordinates: [[lat, lon], ...] — ordered list of GPS points
        distance_m: total Euclidean length in meters
        safety_score: average risk score along the route
        safety_grade: letter grade
        node_count: number of nodes in path
        travel_time_s: estimated travel time in seconds
    """
    coordinates = []
    total_distance = 0.0
    total_travel_time = 0.0
    risk_scores = []

    for node_id in path_nodes:
        node_data = G.nodes[node_id]
        coordinates.append([node_data["y"], node_data["x"]])  # [lat, lon]

    # Accumulate edge stats along the path
    for i in range(len(path_nodes) - 1):
        u, v = path_nodes[i], path_nodes[i + 1]
        # Get best key (in case of multi-edges, pick lowest safety_cost)
        if G.has_edge(u, v):
            edges = G[u][v]
            best_key = min(
                edges.keys(),
                key=lambda k: edges[k].get("safety_cost", float("inf"))
            )
            edge_data = edges[best_key]

            total_distance += edge_data.get("length", 0.0)
            total_travel_time += edge_data.get("travel_time", 0.0)

            risk = edge_data.get("risk_score")
            if risk is not None:
                risk_scores.append(risk)

    avg_risk = sum(risk_scores) / len(risk_scores) if risk_scores else 0.5

    return {
        "coordinates": coordinates,
        "distance_m": round(total_distance, 1),
        "distance_km": round(total_distance / 1000, 2),
        "travel_time_s": round(total_travel_time),
        "travel_time_min": round(total_travel_time / 60, 1),
        "safety_score": round(avg_risk, 4),
        "safety_grade": get_safety_grade(avg_risk),
        "node_count": len(path_nodes),
    }


def compare_routes(safe_route: dict, fast_route: dict) -> dict:
    """
    Returns a comparison summary between safe and fast routes.
    """
    dist_overhead_m = safe_route["distance_m"] - fast_route["distance_m"]
    time_overhead_s = safe_route["travel_time_s"] - fast_route["travel_time_s"]
    risk_improvement = fast_route["safety_score"] - safe_route["safety_score"]

    return {
        "safe_route_extra_distance_m": round(dist_overhead_m, 1),
        "safe_route_extra_distance_km": round(dist_overhead_m / 1000, 2),
        "safe_route_extra_time_s": round(time_overhead_s),
        "safe_route_extra_time_min": round(time_overhead_s / 60, 1),
        "risk_score_improvement": round(risk_improvement, 4),
        "risk_grade_safe": safe_route["safety_grade"],
        "risk_grade_fast": fast_route["safety_grade"],
        "safer_by_percentage": round(risk_improvement * 100, 1),
    }
