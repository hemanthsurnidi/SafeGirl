---
title: SafeGirl
emoji: 🛡️
colorFrom: green
colorTo: blue
sdk: docker
pinned: false
---

# 🛡️ SafeGirl — Hyderabad Safe Route Navigator

A web application that shows **two competing routes** between any two points in Hyderabad:

| Route | Color | Algorithm | Description |
|-------|-------|-----------|-------------|
| 🟩 **Safest Route** | Green | Custom Dijkstra (OSMnx + NetworkX) | Penalizes high-crime zones, poorly lit areas, isolated stretches |
| 🟦 **Fastest Route** | Blue | Google Directions API | Standard time/distance optimized routing |

---

## 📋 Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.10+ | Backend runtime |
| pip | Latest | Package manager |
| Node / npm | Not required | (Pure vanilla JS frontend) |
| Google Maps API Key | — | Maps display + autocomplete |
| ~500 MB RAM | — | Graph computation |
| ~300 MB disk | — | Graph cache file |

---

## 🔑 Step 1: Get a Google Maps API Key

1. Go to [https://console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (e.g., "SafeGirl")
3. Go to **APIs & Services → Library** and enable:
   - ✅ **Maps JavaScript API**
   - ✅ **Places API**
   - ✅ **Directions API**
4. Go to **APIs & Services → Credentials → Create Credentials → API Key**
5. Copy your key
6. **Open `frontend/index.html`** and replace `YOUR_GOOGLE_MAPS_API_KEY`:
   ```html
   window.GOOGLE_MAPS_API_KEY = "AIzaSy...your_actual_key_here...";
   ```

> **Security**: In the Google Console, restrict your key to:
> - HTTP referrers: `localhost:*` for development, your domain for production

---

## 🚀 Step 2: Local Setup (Fastest Way)

### 2a. Install Python Dependencies

```powershell
cd SafeGirl\backend
pip install -r requirements.txt
```

> ⚠️ On Windows, `osmnx` may need Visual C++ Build Tools. If you get a build error:
> ```powershell
> pip install --upgrade pip wheel setuptools
> pip install osmnx --no-build-isolation
> ```

### 2b. Pre-build the Graph (One-time, Required)

This downloads Hyderabad's road network from OpenStreetMap and applies safety weights.

```powershell
cd SafeGirl
python scripts\build_graph.py
```

**Expected output:**
```
SafeGirl — Graph Builder
Downloading road graph for 'Hyderabad, Telangana, India'...
Graph downloaded in 312.4s — 52,847 nodes, 128,491 edges
Computing safety weights for all edges...
✅ Graph build complete!
   Nodes:  52,847
   Edges:  128,491
   Cache: backend\data\hyderabad_graph.graphml
```

> ⏱️ **First run takes 5–20 minutes** depending on your internet speed.
> Subsequent runs load the cached `.graphml` file in **2–5 seconds**.

### 2c. Start the Backend

```powershell
cd SafeGirl\backend
uvicorn main:app --reload --port 8000
```

Verify it's running:
```
http://localhost:8000/health
http://localhost:8000/docs    ← Interactive API docs
```

### 2d. Serve the Frontend

Open a **new terminal**:

```powershell
cd SafeGirl\frontend
python -m http.server 5500
```

**Open your browser**: [http://localhost:5500](http://localhost:5500)

---

## 🗺️ Optional: Harvest Live OSM Data

Enrich the safety database with fresh OpenStreetMap infrastructure data:

```powershell
cd SafeGirl
python scripts\harvest_osm_data.py --output-dir backend\data
```

This fetches police stations, metro stations, hospitals, water bodies, and industrial zones from Overpass API and saves them to:
- `backend/data/osm_positive_anchors.json`
- `backend/data/osm_negative_anchors.json`

After harvesting, **rebuild the graph** to apply the new anchors:
```powershell
python scripts\build_graph.py --force-rebuild
```

---

## 🐳 Docker (Local with Docker Desktop)

### Prerequisites
- [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/)

### Run Everything with One Command

```powershell
cd SafeGirl
docker-compose up --build
```

- Frontend: [http://localhost:5500](http://localhost:5500)
- Backend:  [http://localhost:8000](http://localhost:8000)
- Backend docs: [http://localhost:8000/docs](http://localhost:8000/docs)

> ⚠️ The first build downloads all dependencies and the Hyderabad graph.
> The graph is stored in a Docker volume (`graph-cache`) and survives container restarts.

Stop services:
```powershell
docker-compose down
```

---

## ☁️ Deploy to Railway (Free Public Link)

[Railway.app](https://railway.app) offers a free tier with 500 hours/month — enough for demos.

### Step 1: Push to GitHub

```powershell
cd SafeGirl
git init
git add .
git commit -m "Initial SafeGirl commit"
# Create a repo on GitHub, then:
git remote add origin https://github.com/YOUR_USERNAME/safegirl.git
git push -u origin main
```

### Step 2: Deploy Backend on Railway

1. Go to [https://railway.app](https://railway.app) → **New Project → Deploy from GitHub Repo**
2. Select your `safegirl` repo
3. Railway auto-detects the `Dockerfile` — click **Deploy**
4. Go to **Settings → Networking → Generate Domain** to get a public URL like:
   `https://safegirl-backend-production.up.railway.app`

### Step 3: Set Environment Variable

In Railway → Variables, add:
```
PORT=8000
```

### Step 4: Update Frontend to Point to Railway Backend

Open `frontend/index.html` and change:
```javascript
// Before:
window.BACKEND_URL = "http://localhost:8000";

// After:
window.BACKEND_URL = "https://safegirl-backend-production.up.railway.app";
```

### Step 5: Deploy Frontend (Netlify — Free)

1. Go to [https://netlify.com](https://netlify.com) → **Add new site → Deploy manually**
2. Drag and drop your `frontend/` folder
3. Get a public URL like: `https://safegirl-hyd.netlify.app`

> 🔑 **Don't forget**: Update your Google Maps API key restrictions to allow the Netlify domain.

---

## ☁️ Alternative: Deploy to Render (Free Tier)

### Backend on Render

1. Go to [https://render.com](https://render.com) → **New → Web Service**
2. Connect your GitHub repo
3. Settings:
   - **Runtime**: Docker
   - **Port**: 8000
   - **Instance Type**: Free (512 MB RAM — may be tight; Starter recommended)
4. Click **Deploy**

> ⚠️ Render's free tier spins down after 15 minutes of inactivity. First request after sleep takes ~30 seconds.

---

## 📡 API Reference

### `GET /health`
```json
{
  "status": "ok",
  "graph_loaded": true,
  "scorer_loaded": true,
  "message": "..."
}
```

### `GET /safest-route`

Parameters:
| Param | Type | Example | Description |
|-------|------|---------|-------------|
| `origin_lat` | float | `17.4486` | Source latitude |
| `origin_lon` | float | `78.3908` | Source longitude |
| `dest_lat` | float | `17.3616` | Destination latitude |
| `dest_lon` | float | `78.4747` | Destination longitude |

Example:
```
GET /safest-route?origin_lat=17.4486&origin_lon=78.3908&dest_lat=17.3616&dest_lon=78.4747
```

Response:
```json
{
  "status": "ok",
  "route_type": "safest",
  "coordinates": [[17.4486, 78.3908], [17.4450, 78.3925], ...],
  "distance_m": 12450.5,
  "distance_km": 12.45,
  "travel_time_s": 1680,
  "travel_time_min": 28.0,
  "safety_score": 0.312,
  "safety_grade": "B",
  "node_count": 87
}
```

### `GET /risk-score`
```
GET /risk-score?lat=17.3616&lon=78.4747
```
Returns the computed risk score (0–1) for any specific GPS coordinate.

---

## 🧮 Algorithm Details

### Cost Function

For every road edge in the Hyderabad OSM graph:

```
safety_cost = length_m × (1 + ALPHA × crime_score + BETA × infra_score)
```

Where:
- `length_m` = edge length in meters
- `crime_score` = zonal crime index at the edge midpoint [0–1]
- `infra_score` = infrastructure risk (lighting, isolation) [0–1]
- `ALPHA = 3.0` = crime penalty multiplier
- `BETA = 2.0` = infrastructure risk multiplier

### Safety Score Components

| Component | Weight | Source |
|-----------|--------|--------|
| Zonal crime index | 40% | `safety_seed.json` — 40+ Hyderabad zones |
| Inverted lighting score | 25% | Zone seed + SafetiPin mock audits |
| Inverted crowd/openness | 25% | Zone seed data |
| SafetiPin composite audit | 10% | `safetipin_mock.json` — 20 audit points |
| Positive anchor bonus | −15% max | OSM: police, metro, hospitals |
| Negative anchor penalty | +20% max | OSM: lakes, rivers, industrial |

### Safety Grade Scale

| Grade | Risk Score | Meaning |
|-------|-----------|---------|
| A | < 0.20 | Very safe — well-lit, active, police presence |
| B | 0.20–0.35 | Safe — good infrastructure |
| C | 0.35–0.50 | Moderate — normal caution |
| D | 0.50–0.65 | Elevated — poor lighting or isolated |
| F | > 0.65 | High risk — avoid if possible |

---

## 📁 Project Structure

```
SafeGirl/
├── backend/
│   ├── main.py              # FastAPI app
│   ├── graph_builder.py     # OSMnx graph + safety edge weights
│   ├── safety_scorer.py     # Risk score computation
│   ├── router.py            # Dijkstra safest/fastest path
│   ├── requirements.txt
│   └── data/
│       ├── safety_seed.json        # 40+ zone risk database
│       ├── safetipin_mock.json     # 20 audit point scores
│       └── hyderabad_graph.graphml # Auto-generated graph cache
├── frontend/
│   ├── index.html           # Single-page app
│   ├── app.js               # Google Maps + route logic
│   └── styles.css           # Glassmorphism dark theme
├── scripts/
│   ├── harvest_osm_data.py  # OSM Overpass data harvesting
│   └── build_graph.py       # One-time graph pre-build
├── Dockerfile
├── docker-compose.yml
├── nginx.conf
└── README.md
```

---

## ❓ Troubleshooting

### `ModuleNotFoundError: No module named 'osmnx'`
```powershell
pip install osmnx
```

### `RuntimeError: 'CRS' object has no attribute ...` (pyproj version issue)
```powershell
pip install --upgrade pyproj osmnx
```

### Graph download hangs / times out
- OSM servers can be slow. Try again during off-peak hours (early morning IST).
- Use `--force-rebuild` flag only if the cache is corrupt.

### Backend returns 400: "outside Hyderabad bounds"
- Only coordinates within the bounding box `17.20°N–17.60°N, 78.20°E–78.65°E` are supported.
- The Places Autocomplete is restricted to India — make sure you select Hyderabad locations.

### Google Maps shows blank / "For development purposes only" watermark
- Your API key is missing or invalid. Ensure billing is enabled on the Google Cloud project.

### Safest route is exactly the same as the fastest route
- This can happen if ALPHA/BETA are too low. Edit `ALPHA` and `BETA` in `graph_builder.py` and rebuild.

---

## 📜 Data Sources & Disclaimers

- **Road Network**: OpenStreetMap contributors (ODbL License)
- **Zonal Safety Scores**: Proxy data synthesized from Hyderabad City Police, Cyberabad Police, and Rachakonda Police Annual Reports (2022–2024); SafetiPin Hyderabad audit summaries; NCRB state-level statistics
- **SafetiPin Mock Data**: Research proxy only — not official SafetiPin data
- **OSM Anchors**: Live from OpenStreetMap Overpass API

> ⚠️ **This is a research/demo application.** The safety scores are proxy data and **should not be used for actual personal safety decisions.** For real safety information, contact local police authorities.
