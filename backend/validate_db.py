import sqlite3
import os
import math

# Paths
BASE = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE, "data", "crime_records.db")

# Major water bodies in Telangana (lat, lng, radius in meters to exclude)
WATER_BODIES = [
    {"name": "Hussain Sagar Lake", "lat": 17.4239, "lng": 78.4738, "radius": 950},
    {"name": "Himayat Sagar Lake", "lat": 17.3195, "lng": 78.3582, "radius": 2200},
    {"name": "Osman Sagar Lake", "lat": 17.3828, "lng": 78.2917, "radius": 2200},
    {"name": "Singur Reservoir", "lat": 17.7983, "lng": 77.9239, "radius": 3500},
    {"name": "Nizam Sagar", "lat": 18.5714, "lng": 77.9333, "radius": 4000},
    {"name": "Nagarjuna Sagar Reservoir", "lat": 16.5833, "lng": 79.3167, "radius": 5000},
    {"name": "Srisailam Reservoir", "lat": 16.1000, "lng": 78.9000, "radius": 5000},
]

def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371000  # meters
    p = math.pi / 180
    a = 0.5 - math.cos((lat2 - lat1) * p)/2 + \
        math.cos(lat1 * p) * math.cos(lat2 * p) * \
        (1 - math.cos((lon2 - lon1) * p)) / 2
    return 2 * R * math.asin(math.sqrt(a))

def validate_and_clean_db():
    if not os.path.exists(DB_FILE):
        print(f"Error: Database {DB_FILE} not found!")
        return

    print(f"Opening database: {DB_FILE}")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # 1. Alter schema to add columns if they do not exist
    cursor.execute("PRAGMA table_info(crime_hotspots)")
    columns = [c[1] for c in cursor.fetchall()]
    
    if "confidence_score" not in columns:
        print("Adding confidence_score column to crime_hotspots...")
        cursor.execute("ALTER TABLE crime_hotspots ADD COLUMN confidence_score REAL DEFAULT 0.8")
    if "verified" not in columns:
        print("Adding verified column to crime_hotspots...")
        cursor.execute("ALTER TABLE crime_hotspots ADD COLUMN verified INTEGER DEFAULT 1")
    conn.commit()

    # 2. Fetch all hotspots for validation
    cursor.execute("SELECT id, location_name, crime_category, latitude, longitude, source FROM crime_hotspots")
    rows = cursor.fetchall()
    print(f"Inspecting {len(rows)} records for validation...")

    to_delete = []
    to_update = []
    seen_coords = {} # {(lat_bucket, lng_bucket, category): id} for deduplication

    removed_out_of_bounds = 0
    removed_water_bodies = 0
    removed_duplicates = 0

    for r in rows:
        r_id, name, category, lat, lng, source = r

        # A. Boundary verification (Telangana State bounds)
        if not (15.80 <= lat <= 19.90 and 77.00 <= lng <= 81.50):
            to_delete.append(r_id)
            removed_out_of_bounds += 1
            continue

        # B. Water body exclusion check
        in_water = False
        for wb in WATER_BODIES:
            dist = haversine_distance(lat, lng, wb["lat"], wb["lng"])
            if dist < wb["radius"]:
                in_water = True
                print(f"Flagged point {r_id} ({lat}, {lng}) inside {wb['name']} (Distance: {dist:.1f}m)")
                break
        
        if in_water:
            to_delete.append(r_id)
            removed_water_bodies += 1
            continue

        # C. Deduplication check (within 10 meters, ~0.0001 deg)
        # We bucket coordinates to 4 decimal places (~11 meters) to catch duplicates quickly
        bucket_lat = round(lat, 4)
        bucket_lng = round(lng, 4)
        dup_key = (bucket_lat, bucket_lng, category)
        
        if dup_key in seen_coords:
            to_delete.append(r_id)
            removed_duplicates += 1
            continue
        else:
            seen_coords[dup_key] = r_id

        # D. Assign confidence score & verified attributes
        confidence = 0.80
        verified = 1
        
        if source == "Telangana Police Portal":
            confidence = 0.95
        elif source == "SafetiPin Audit Summary":
            confidence = 0.90
        elif source == "NCRB State Statistics":
            confidence = 0.85
        elif source == "Citizen Safety App Report":
            confidence = 0.55
            verified = 0
            
        to_update.append((confidence, verified, r_id))

    # Perform updates & deletions
    print(f"Applying updates and cleaning deletions...")
    
    if to_update:
        cursor.executemany("UPDATE crime_hotspots SET confidence_score = ?, verified = ? WHERE id = ?", to_update)
        print(f"Updated {len(to_update)} records with confidence scores.")

    if to_delete:
        # Delete in chunks
        chunk_size = 500
        for i in range(0, len(to_delete), chunk_size):
            chunk = to_delete[i:i+chunk_size]
            cursor.execute(f"DELETE FROM crime_hotspots WHERE id IN ({','.join(['?']*len(chunk))})", chunk)
        print(f"Successfully deleted {len(to_delete)} invalid records:")
        print(f"  - Outside state bounds: {removed_out_of_bounds}")
        print(f"  - Inside water reservoirs: {removed_water_bodies}")
        print(f"  - Duplicate coordinates: {removed_duplicates}")

    # Rebuild database indexes & shrink size
    print("Vacuuming and optimizing database...")
    conn.commit()
    # VACUUM must run outside a transaction block
    conn.isolation_level = None
    cursor.execute("VACUUM")
    conn.close()
    print("Database validation and clean verification COMPLETE.")

if __name__ == "__main__":
    validate_and_clean_db()
