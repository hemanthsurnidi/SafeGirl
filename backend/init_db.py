import sqlite3
import os
import random
import math
from datetime import datetime, timedelta

# Paths
BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
DB_FILE = os.path.join(DATA_DIR, "crime_records.db")
os.makedirs(DATA_DIR, exist_ok=True)

# Coordinates of major Telangana hubs for clustered spatial generation
TELANGANA_HUBS = [
    {"name": "Hyderabad Center", "lat": 17.3850, "lng": 78.4867, "weight": 0.50},  # 50% density in capital area
    {"name": "Secunderabad Area", "lat": 17.4399, "lng": 78.4983, "weight": 0.08},
    {"name": "Warangal City", "lat": 17.9784, "lng": 79.5941, "weight": 0.08},
    {"name": "Karimnagar Town", "lat": 18.4386, "lng": 79.1288, "weight": 0.05},
    {"name": "Nizamabad City", "lat": 18.6725, "lng": 78.0941, "weight": 0.05},
    {"name": "Khammam City", "lat": 17.2473, "lng": 80.1514, "weight": 0.04},
    {"name": "Nalgonda Town", "lat": 17.0500, "lng": 79.2667, "weight": 0.03},
    {"name": "Mahabubnagar Town", "lat": 16.7333, "lng": 77.9833, "weight": 0.03},
    {"name": "Adilabad Town", "lat": 19.6667, "lng": 78.5333, "weight": 0.02},
    {"name": "Siddipet City", "lat": 18.1018, "lng": 78.8524, "weight": 0.02},
    {"name": "Suryapet City", "lat": 17.1500, "lng": 79.6167, "weight": 0.02},
    {"name": "Ramagundam City", "lat": 18.7594, "lng": 79.4452, "weight": 0.02},
    {"name": "Nirmal Town", "lat": 19.0964, "lng": 78.3429, "weight": 0.01},
    {"name": "Bhongir Town", "lat": 17.5167, "lng": 78.8833, "weight": 0.01},
    {"name": "Vikarabad Town", "lat": 17.3333, "lng": 77.9000, "weight": 0.01},
    {"name": "Sangareddy Town", "lat": 17.6167, "lng": 78.0833, "weight": 0.01},
    {"name": "Medak Town", "lat": 18.0333, "lng": 78.2667, "weight": 0.01},
    {"name": "Wanaparthy Town", "lat": 16.3667, "lng": 78.0667, "weight": 0.01},
]

CRIME_CATEGORIES = [
    {"category": "Theft", "severities": [("Low", 2.0), ("Medium", 4.5)]},
    {"category": "Chain Snatching", "severities": [("Medium", 5.5), ("High", 7.5)]},
    {"category": "Eve Teasing / Harassment", "severities": [("Medium", 6.0), ("High", 8.0)]},
    {"category": "Physical Assault", "severities": [("High", 8.5), ("High", 9.5)]},
    {"category": "Unlit isolated corridor report", "severities": [("Low", 3.0), ("Medium", 5.0)]},
    {"category": "Road Safety / Accident Hotspot", "severities": [("Low", 2.5), ("Medium", 4.8)]},
]

FACILITY_TYPES = [
    {"type": "police", "names": ["Police Station", "Police Outpost", "She Teams Hub", "Commissioner's Office"]},
    {"type": "hospital", "names": ["Government Hospital", "Apollo Clinic", "Care Hospital", "Area Hospital"]},
    {"type": "clinic", "names": ["Community Clinic", "Primary Health Centre", "Private Clinic"]},
    {"type": "fire_station", "names": ["District Fire Station", "Emergency Rescue Centre"]},
    {"type": "pharmacy", "names": ["Apollo Pharmacy", "MedPlus Pharmacy", "24/7 Medical Store"]},
    {"type": "petrol_pump", "names": ["IOCL Petrol Station", "HP Petrol Station", "Bharat Petroleum"]},
    {"type": "transit", "names": ["Metro Station", "Bus Depot", "Railway Station", "Bus Stop"]},
    {"type": "emergency_shelter", "names": ["Rain Basera Rescue Shelter", "Women Empowerment Shelter", "Aasra Shelter"]},
    {"type": "government_office", "names": ["Mandal Revenue Office", "Municipal Corporation Office", "Collectorate"]}
]

SOURCES = ["Telangana Police Portal", "SafetiPin Audit Summary", "NCRB State Statistics", "Citizen Safety App Report"]

def generate_random_coords(center_lat, center_lng, dist_deg=0.08):
    # Gaussian distribution to simulate realistic clustering around urban hotspots
    u = random.gauss(0, 1) * dist_deg
    v = random.gauss(0, 1) * dist_deg
    return round(center_lat + u, 6), round(center_lng + v, 6)

def init_db():
    print(f"Initializing database at: {DB_FILE}")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Drop tables to recreate cleanly
    cursor.execute("DROP TABLE IF EXISTS crime_hotspots")
    cursor.execute("DROP TABLE IF EXISTS public_facilities")

    # Create crime hotspots table
    cursor.execute("""
    CREATE TABLE crime_hotspots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        location_name TEXT,
        crime_category TEXT,
        severity TEXT,
        risk_score REAL,
        latitude REAL,
        longitude REAL,
        timestamp TEXT,
        source TEXT,
        confidence_score REAL DEFAULT 0.8,
        verified INTEGER DEFAULT 1
    )
    """)

    # Create public facilities table
    cursor.execute("""
    CREATE TABLE public_facilities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        type TEXT,
        latitude REAL,
        longitude REAL,
        details TEXT
    )
    """)

    # Generate 10,000 Crime Hotspots
    print("Generating 10,000 crime records...")
    crime_records = []
    base_date = datetime.now() - timedelta(days=730) # records from past 2 years
    
    # Helper to calculate confidence and verified values
    def get_source_meta(src):
        if src == "Telangana Police Portal":
            return 0.95, 1
        elif src == "SafetiPin Audit Summary":
            return 0.90, 1
        elif src == "NCRB State Statistics":
            return 0.85, 1
        else: # Citizen Safety App Report
            return 0.55, 0

    # Pre-populate specific known high risk hotspots from main.py for exact testing matches
    seed_hotspots = [
        ("Madhapur High-Risk Hub", "Eve Teasing / Harassment", "Medium", 6.5, 17.4485, 78.3741, "Citizen Safety App Report"),
        ("Charminar Old City Area", "Physical Assault", "High", 8.8, 17.3616, 78.4747, "Telangana Police Portal"),
        ("Afzalgunj Market Junction", "Chain Snatching", "High", 8.2, 17.3773, 78.4789, "Telangana Police Portal"),
        ("Gachibowli Dark Underpass", "Unlit isolated corridor report", "High", 7.8, 17.4082, 78.3392, "SafetiPin Audit Summary"),
        ("Musi River Belt", "Physical Assault", "High", 9.2, 17.3600, 78.4800, "Telangana Police Portal"),
        ("Warangal Town Center", "Theft", "Medium", 4.2, 17.9784, 79.5941, "Telangana Police Portal"),
        ("Karimnagar Town Center", "Eve Teasing / Harassment", "Medium", 5.5, 18.4386, 79.1288, "SafetiPin Audit Summary")
    ]
    for name, cat, sev, risk, lat, lng, src in seed_hotspots:
        t = base_date + timedelta(seconds=random.randint(0, 63072000))
        conf, ver = get_source_meta(src)
        crime_records.append((name, cat, sev, risk, lat, lng, t.strftime("%Y-%m-%d %H:%M:%S"), src, conf, ver))

    # Fill up to 10,000
    while len(crime_records) < 10000:
        # Choose hub based on weight
        hub = random.choices(TELANGANA_HUBS, weights=[h["weight"] for h in TELANGANA_HUBS])[0]
        # Spread slightly more in larger cities
        spread = 0.12 if hub["name"] == "Hyderabad Center" else 0.06
        lat, lng = generate_random_coords(hub["lat"], hub["lng"], spread)
        
        # Verify coordinates lie inside Telangana general bounding box
        if not (15.80 <= lat <= 19.90 and 77.00 <= lng <= 81.50):
            continue

        cat_info = random.choice(CRIME_CATEGORIES)
        sev, risk_base = random.choice(cat_info["severities"])
        risk_score = round(risk_base + random.uniform(-0.5, 0.5), 2)
        risk_score = max(1.0, min(10.0, risk_score)) # clamp
        
        loc_name = f"{hub['name']} Vicinity Spot #{len(crime_records)}"
        t = base_date + timedelta(seconds=random.randint(0, 63072000))
        src = random.choice(SOURCES)
        conf, ver = get_source_meta(src)
        
        crime_records.append((loc_name, cat_info["category"], sev, risk_score, lat, lng, t.strftime("%Y-%m-%d %H:%M:%S"), src, conf, ver))

    cursor.executemany("""
    INSERT INTO crime_hotspots (location_name, crime_category, severity, risk_score, latitude, longitude, timestamp, source, confidence_score, verified)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, crime_records)

    # Generate 1,500 Public Facilities along route corridors
    print("Generating 1,500 public facilities...")
    facilities = []
    
    # Pre-populate some exact seed points
    seed_fac = [
        ("She Teams Hitech City Outpost", "police", 17.4435, 78.3772, "Active patrolling, Emergency response"),
        ("Secunderabad Metro Station", "transit", 17.4431, 78.5015, "Fully illuminated, CCTV covered, She Teams point"),
        ("HYD City Police HQ", "police", 17.4086, 78.4712, "24/7 Security command center"),
        ("Hitech City Metro Station", "transit", 17.4440, 78.3789, "Fully lit commercial metro transit corridor"),
        ("Warangal Police HQ", "police", 17.9700, 79.6000, "24/7 Active police control room"),
        ("Karimnagar Bus Depot", "transit", 18.4330, 79.1250, "Major transit point, active guard presence")
    ]
    for name, type_val, lat, lng, details in seed_fac:
        facilities.append((name, type_val, lat, lng, details))

    while len(facilities) < 1500:
        hub = random.choice(TELANGANA_HUBS)
        lat, lng = generate_random_coords(hub["lat"], hub["lng"], 0.08)
        
        # Verify Telangana bounds
        if not (15.80 <= lat <= 19.90 and 77.00 <= lng <= 81.50):
            continue

        fac_info = random.choice(FACILITY_TYPES)
        type_val = fac_info["type"]
        fac_name = f"{hub['name']} {random.choice(fac_info['names'])} #{len(facilities)}"
        
        details = "Fully lit corridor, guard on-site, CCTV active" if type_val in ["police", "transit", "hospital"] else "Standard public facility, active daytime hours"
        if type_val == "hospital":
            details += ", Emergency 24/7"
        elif type_val == "police":
            details += ", She Teams Emergency Anchor"

        facilities.append((fac_name, type_val, lat, lng, details))

    cursor.executemany("""
    INSERT INTO public_facilities (name, type, latitude, longitude, details)
    VALUES (?, ?, ?, ?, ?)
    """, facilities)

    # Indexes for performance
    print("Building indexes...")
    cursor.execute("CREATE INDEX idx_crime_coords ON crime_hotspots(latitude, longitude)")
    cursor.execute("CREATE INDEX idx_crime_filters ON crime_hotspots(crime_category, severity, risk_score)")
    cursor.execute("CREATE INDEX idx_fac_coords ON public_facilities(latitude, longitude)")
    cursor.execute("CREATE INDEX idx_fac_type ON public_facilities(type)")

    conn.commit()
    conn.close()
    print("Database initialization COMPLETE. SQLite ready.")

if __name__ == "__main__":
    init_db()
