"""
build_graph.py
--------------
One-time script to download and cache the Hyderabad road graph with safety weights.
Run this BEFORE starting the backend server to ensure fast startup.

Usage:
    cd SafeGirl
    python scripts/build_graph.py [--force-rebuild]

This script:
1. Downloads Hyderabad's drivable road network from OpenStreetMap
2. Initializes the SafetyScorer (loads seed data + fetches OSM anchors)
3. Applies custom safety_cost weights to every edge
4. Caches the result as hyderabad_graph.graphml

Expected runtime: 5–20 minutes depending on internet speed and Overpass API load.
Disk space: ~150–300 MB for the .graphml cache file.
RAM usage: ~1–2 GB during computation.
"""

import argparse
import logging
import os
import sys
import time

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("build_graph")


def main():
    parser = argparse.ArgumentParser(
        description="Pre-build and cache the Hyderabad safety-weighted road graph"
    )
    parser.add_argument(
        "--force-rebuild",
        action="store_true",
        help="Force re-download even if cached graph exists",
    )
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("SafeGirl — Graph Builder")
    logger.info("=" * 60)

    # Check for existing cache
    cache_path = os.path.join(
        os.path.dirname(__file__), "..", "backend", "data", "hyderabad_graph.graphml"
    )
    cache_exists = os.path.exists(cache_path)

    if cache_exists and not args.force_rebuild:
        cache_size_mb = os.path.getsize(cache_path) / (1024 * 1024)
        logger.info(f"✓ Cached graph found: {cache_path} ({cache_size_mb:.1f} MB)")
        logger.info("Use --force-rebuild to re-download.")
        resp = input("Re-use existing cache? [Y/n]: ").strip().lower()
        if resp not in ("n", "no"):
            logger.info("Using existing cache. Running weight update only...")
            force = False
        else:
            force = True
    else:
        force = args.force_rebuild or not cache_exists

    logger.info("\nStep 1: Initializing SafetyScorer...")
    start = time.time()
    from safety_scorer import SafetyScorer
    scorer = SafetyScorer()
    logger.info(f"SafetyScorer ready in {time.time() - start:.1f}s")

    logger.info("\nStep 2: Loading/building road graph...")
    logger.info("⚠️  This may take 5–20 minutes for the first download.")
    logger.info("    Subsequent runs load the cached file in seconds.")

    start_total = time.time()
    from graph_builder import load_or_build_graph, get_graph_stats

    G = load_or_build_graph(scorer=scorer, force_rebuild=force)

    total_time = time.time() - start_total

    logger.info("\nStep 3: Verifying graph...")
    stats = get_graph_stats(G)

    logger.info("\n" + "=" * 60)
    logger.info("✅ Graph build complete!")
    logger.info(f"   Nodes:  {stats['nodes']:,}")
    logger.info(f"   Edges:  {stats['edges']:,}")
    logger.info(f"   Place:  {stats['place']}")
    logger.info(f"   Mode:   {stats['network_type']}")
    logger.info(f"   Alpha:  {stats['alpha']} (crime penalty)")
    logger.info(f"   Beta:   {stats['beta']} (infra penalty)")

    if stats.get("risk_score_stats"):
        rs = stats["risk_score_stats"]
        logger.info(f"   Risk scores — min: {rs['min']}, max: {rs['max']}, mean: {rs['mean']}")

    logger.info(f"   Total time: {total_time:.1f}s ({total_time/60:.1f} min)")
    logger.info(f"   Cache: {cache_path}")
    logger.info("=" * 60)
    logger.info("\nYou can now start the backend server:")
    logger.info("    cd backend && uvicorn main:app --reload --port 8000")


if __name__ == "__main__":
    main()
