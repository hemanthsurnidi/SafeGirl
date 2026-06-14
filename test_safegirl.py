import unittest
import requests

BASE_URL = "http://localhost:8000"

class TestSafeGirlPlanner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Verify the API is healthy and reachable
        try:
            r = requests.get(f"{BASE_URL}/api/health", timeout=5)
            if r.status_code != 200:
                raise RuntimeError("Backend server returned non-200 status for health check")
        except Exception as e:
            raise RuntimeError(f"Backend server is not running or unreachable at {BASE_URL}. Start the server before running tests. Error: {e}")

    def test_health_endpoint(self):
        r = requests.get(f"{BASE_URL}/api/health")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data.get("engine_ready"), "Routing engine should be ready")
        self.assertIn("crime_hotspots_count", data)
        self.assertIn("facilities_count", data)

    def test_autocomplete_exact_and_prefix(self):
        # Test exact match for "Madhapur"
        r = requests.get(f"{BASE_URL}/api/autocomplete", params={"q": "Madhapur"})
        self.assertEqual(r.status_code, 200)
        hits = r.json()
        self.assertTrue(len(hits) > 0, "Should return autocomplete results for Madhapur")
        self.assertIn("Madhapur", hits[0]["display_name"])

        # Test prefix match for "hite"
        r = requests.get(f"{BASE_URL}/api/autocomplete", params={"q": "hite"})
        self.assertEqual(r.status_code, 200)
        hits = r.json()
        self.assertTrue(len(hits) > 0, "Should return autocomplete results for prefix 'hite'")
        names = [item["display_name"].lower() for item in hits]
        self.assertTrue(any("hitech city" in name for name in names), "Hits should contain Hitech City")

    def test_autocomplete_typo_tolerance(self):
        # Test fuzzy / typo tolerance search matching "bus"
        r = requests.get(f"{BASE_URL}/api/autocomplete", params={"q": "bus"})
        self.assertEqual(r.status_code, 200)
        hits = r.json()
        self.assertTrue(len(hits) > 0, "Query 'bus' should return results")
        names = [item["display_name"].lower() for item in hits]
        self.assertTrue(any("bus" in name for name in names), "Should return bus stops/depots/stations")

        # Test query "mad"
        r = requests.get(f"{BASE_URL}/api/autocomplete", params={"q": "mad"})
        self.assertEqual(r.status_code, 200)
        hits = r.json()
        self.assertTrue(len(hits) > 0, "Query 'mad' should return results")
        names = [item["display_name"].lower() for item in hits]
        self.assertTrue(any("madhapur" in name for name in names), "Should return Madhapur")

        # Test query "gach"
        r = requests.get(f"{BASE_URL}/api/autocomplete", params={"q": "gach"})
        self.assertEqual(r.status_code, 200)
        hits = r.json()
        self.assertTrue(len(hits) > 0, "Query 'gach' should return results")
        names = [item["display_name"].lower() for item in hits]
        self.assertTrue(any("gachibowli" in name for name in names), "Should return Gachibowli")

    def test_autocomplete_telangana_districts(self):
        # Districts to test as specified
        districts = [
            ("Hyderabad", "hyderabad"),
            ("Warangal", "warangal"),
            ("Karimnagar", "karimnagar"),
            ("Nizamabad", "nizamabad"),
            ("Khammam", "khammam"),
            ("Siddipet", "siddipet"),
            ("Adilabad", "adilabad"),
            ("Mahabubnagar", "mahabubnagar")
        ]

        for display_name, query in districts:
            r = requests.get(f"{BASE_URL}/api/autocomplete", params={"q": query})
            self.assertEqual(r.status_code, 200, f"Query for '{query}' should succeed")
            hits = r.json()
            self.assertTrue(len(hits) > 0, f"Query for '{query}' should return results")
            
            # The suggestions must fall inside Telangana bounds
            lat = hits[0]["lat"]
            lon = hits[0]["lon"]
            self.assertTrue(15.80 <= lat <= 19.90, f"Lat {lat} for '{display_name}' must be in Telangana bounds")
            self.assertTrue(77.00 <= lon <= 81.50, f"Lon {lon} for '{display_name}' must be in Telangana bounds")
            
            names = [item["display_name"].lower() for item in hits]
            self.assertTrue(any(query in name for name in names), f"Suggestions for '{query}' should contain the keyword")

    def test_route_generation_and_scoring(self):
        # Route from Madhapur (17.4483, 78.3915) to Charminar (17.3616, 78.4747)
        params = {
            "start_lat": 17.4483, "start_lng": 78.3915,
            "end_lat": 17.3616, "end_lng": 78.4747,
            "alpha": 5.0, "beta": 5.0
        }
        r = requests.get(f"{BASE_URL}/api/route", params=params)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data.get("status"), "success")

        # Verify all three routes are present
        for key in ["fastest", "safest", "balanced"]:
            self.assertIn(key, data)
            route_data = data[key]
            self.assertIn("route", route_data)
            self.assertIn("metrics", route_data)
            self.assertIn("facilities", route_data)

            metrics = route_data["metrics"]
            self.assertIn("distance_km", metrics)
            self.assertIn("duration", metrics)
            self.assertIn("safety_rating", metrics)
            self.assertIn("avg_crime_score", metrics)
            self.assertIn("avg_infra_risk_score", metrics)
            self.assertIn("travel_score", metrics)
            self.assertIn("balanced_score", metrics)

            # Scores must be out of 10.0
            self.assertTrue(0.0 <= metrics["travel_score"] <= 10.0)
            self.assertTrue(0.0 <= metrics["balanced_score"] <= 10.0)

        # Retrieve scores
        fastest_metrics = data["fastest"]["metrics"]
        safest_metrics = data["safest"]["metrics"]
        balanced_metrics = data["balanced"]["metrics"]

        # 1. Safest Route checks:
        self.assertGreaterEqual(
            safest_metrics["safety_rating"],
            fastest_metrics["safety_rating"],
            "Safest route should have a safety rating equal to or higher than the fastest route"
        )
        # 2. Fastest Route checks:
        self.assertLessEqual(
            fastest_metrics["duration"],
            safest_metrics["duration"],
            "Fastest route should have a duration equal to or shorter than the safest route"
        )
        # 3. Balanced Route checks:
        expected_balanced = round(0.5 * balanced_metrics["safety_rating"] + 0.5 * balanced_metrics["travel_score"], 1)
        self.assertAlmostEqual(balanced_metrics["balanced_score"], expected_balanced, places=1)

    def test_route_generation_telangana_wide(self):
        # Test inter-city paths across the state (utilizing OSRM fallback routing)
        paths = [
            # Hyderabad -> Warangal
            {"slat": 17.3850, "slng": 78.4867, "dlat": 17.9784, "dlng": 79.5941},
            # Nizamabad -> Khammam
            {"slat": 18.6725, "slng": 78.0941, "dlat": 17.2473, "dlng": 80.1514},
            # Karimnagar -> Hyderabad
            {"slat": 18.4386, "slng": 79.1288, "dlat": 17.3850, "dlng": 78.4867}
        ]

        for p in paths:
            params = {
                "start_lat": p["slat"], "start_lng": p["slng"],
                "end_lat": p["dlat"], "end_lng": p["dlng"],
                "alpha": 5.0, "beta": 5.0
            }
            r = requests.get(f"{BASE_URL}/api/route", params=params)
            self.assertEqual(r.status_code, 200, f"Route from {p['slat']},{p['slng']} to {p['dlat']},{p['dlng']} failed")
            data = r.json()
            self.assertEqual(data.get("status"), "success")
            for route_type in ["fastest", "safest", "balanced"]:
                self.assertIn(route_type, data)
                route_data = data[route_type]
                self.assertTrue(len(route_data["route"]) > 0, f"Route {route_type} coords empty")
                self.assertIn("metrics", route_data)

if __name__ == "__main__":
    unittest.main()
