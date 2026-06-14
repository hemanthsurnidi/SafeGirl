/**
 * app.js — SafeGirl Hyderabad Route Planner
 *
 * Stack: Leaflet.js + CartoDB tiles (NO Google Maps, NO API key)
 *        OSRM for fastest route (free, open-source)
 *        Python FastAPI backend for safest route (custom Dijkstra)
 *        Nominatim for geocoding (bounded to Hyderabad)
 */

/* ══════════════════════════════════════════════════════════════════
   1. CONSTANTS — Hyderabad bounds (hard lock)
══════════════════════════════════════════════════════════════════ */
const TELANGANA_CENTER = [17.85, 79.15];
const TELANGANA_BOUNDS = L.latLngBounds([15.80, 77.00], [19.90, 81.50]);
const OSRM_URL   = 'https://router.project-osrm.org/route/v1/driving';
const NOM_URL    = 'https://nominatim.openstreetmap.org/search';
const TELANGANA_VBOX   = '77.00,15.80,81.50,19.90';  // Nominatim viewbox

/* ══════════════════════════════════════════════════════════════════
   2. MAP INITIALISATION
══════════════════════════════════════════════════════════════════ */
const map = L.map('map', {
  center:              TELANGANA_CENTER,
  zoom:                8,
  minZoom:             7,
  maxZoom:             19,
  maxBounds:           TELANGANA_BOUNDS,
  maxBoundsViscosity:  1.0,
  zoomControl:         false,
});

L.control.zoom({ position: 'topright' }).addTo(map);

// CartoDB Dark Matter — free, no API key
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>',
  subdomains:  'abcd',
  maxZoom:     19,
  bounds:      TELANGANA_BOUNDS,
}).addTo(map);

/* ══════════════════════════════════════════════════════════════════
   3. STATE
══════════════════════════════════════════════════════════════════ */
let srcCoord = null, dstCoord = null;   // { lat, lng }
let srcMarker = null, dstMarker = null;
let safePolyline = null, fastPolyline = null;

const layers  = { safe: [], risk: [], audit: [], crime: [] };
const layerOn = { safe: true, risk: true, audit: false, crime: true };

/* ══════════════════════════════════════════════════════════════════
   4. CUSTOM ICONS
══════════════════════════════════════════════════════════════════ */
function pinIcon(color, size = 14) {
  return L.divIcon({
    className: '',
    iconSize:    [size, size],
    iconAnchor:  [size / 2, size / 2],
    popupAnchor: [0, -size / 2],
    html: `<div style="
      width:${size}px;height:${size}px;border-radius:50%;
      background:${color};
      border:2.5px solid rgba(255,255,255,.95);
      box-shadow:0 0 12px ${color}99,0 2px 8px rgba(0,0,0,.5)"></div>`,
  });
}

function dotIcon(color, size = 8) {
  return L.divIcon({
    className: '',
    iconSize:    [size, size],
    iconAnchor:  [size / 2, size / 2],
    html: `<div style="
      width:${size}px;height:${size}px;border-radius:50%;
      background:${color};opacity:.85;
      border:1px solid rgba(255,255,255,.35)"></div>`,
  });
}

/* ══════════════════════════════════════════════════════════════════
   5. PIN PLACEMENT
══════════════════════════════════════════════════════════════════ */
function placeStart(lat, lng, label) {
  srcCoord = { lat, lng };
  if (srcMarker) srcMarker.remove();
  srcMarker = L.marker([lat, lng], { icon: pinIcon('#3b82f6') })
    .addTo(map)
    .bindPopup(`<div class="pop-title">📍 Start</div><div class="pop-sub">${label}</div>`);
  document.getElementById('src-in').value = label;
  clearRoutes();
}

function placeDest(lat, lng, label) {
  dstCoord = { lat, lng };
  if (dstMarker) dstMarker.remove();
  dstMarker = L.marker([lat, lng], { icon: pinIcon('#22d3a0') })
    .addTo(map)
    .bindPopup(`<div class="pop-title">🏁 Destination</div><div class="pop-sub">${label}</div>`);
  document.getElementById('dst-in').value = label;
  clearRoutes();
}

/* ══════════════════════════════════════════════════════════════════
   6. QUICK SELECT (12 Hyderabad locations)
══════════════════════════════════════════════════════════════════ */
function qs(which, lat, lng, label) {
  if (which === 'src') { placeStart(lat, lng, label); }
  else                  { placeDest(lat, lng, label); }
  map.setView([lat, lng], 14, { animate: true });
  toast(which === 'src' ? `📍 Start: ${label}` : `🏁 Destination: ${label}`);
}

/* ══════════════════════════════════════════════════════════════════
   7. CLEAR POINT
══════════════════════════════════════════════════════════════════ */
function clearPt(which) {
  if (which === 'src') {
    document.getElementById('src-in').value = '';
    srcCoord = null;
    if (srcMarker) { srcMarker.remove(); srcMarker = null; }
  } else {
    document.getElementById('dst-in').value = '';
    dstCoord = null;
    if (dstMarker) { dstMarker.remove(); dstMarker = null; }
  }
  clearRoutes();
}

/* ══════════════════════════════════════════════════════════════════
   8. MAP CLICK — drop pins sequentially
══════════════════════════════════════════════════════════════════ */
map.on('click', (e) => {
  const { lat, lng } = e.latlng;
  if (!TELANGANA_BOUNDS.contains([lat, lng])) return;
  const label = `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
  if (!srcCoord)      placeStart(lat, lng, label);
  else if (!dstCoord) placeDest(lat, lng, label);
});

/* ══════════════════════════════════════════════════════════════════
   9. GEOCODING AUTOCOMPLETE (Nominatim, locked to Hyderabad)
══════════════════════════════════════════════════════════════════ */
function setupAutocomplete(inputId, suggestId, isSrc) {
  const inp = document.getElementById(inputId);
  const sgBox = document.getElementById(suggestId);
  let timer;

  inp.addEventListener('input', () => {
    clearTimeout(timer);
    const q = inp.value.trim();
    if (q.length < 2) { sgBox.style.display = 'none'; sgBox.innerHTML = ''; return; }

    timer = setTimeout(async () => {
      try {
        const url = `${NOM_URL}?q=${encodeURIComponent(q + ' Telangana India')}`
          + `&format=json&limit=6&countrycodes=in&bounded=1&viewbox=${TELANGANA_VBOX}`;
        const res  = await fetch(url, { headers: { 'Accept-Language': 'en' } });
        const data = await res.json();

        // Keep only results that fall inside Telangana bounding box
        const hits = data.filter(r => TELANGANA_BOUNDS.contains([+r.lat, +r.lon]));

        sgBox.innerHTML = '';
        if (!hits.length) {
          sgBox.innerHTML = `<div class="sg-empty">No Telangana results for "${q}"</div>`;
        } else {
          hits.forEach(r => {
            const parts = r.display_name.split(',');
            const short = parts.slice(0, 3).join(', ').trim();
            const icon  = r.type === 'station' ? '🚉'
                        : r.type === 'police'  ? '🚔'
                        : r.type === 'hospital'? '🏥'
                        : '📍';
            const el = document.createElement('div');
            el.className = 'sg-item';
            el.innerHTML = `<span class="sg-ic">${icon}</span><span class="sg-txt" title="${r.display_name}">${short}</span>`;
            el.addEventListener('mousedown', ev => {
              ev.preventDefault();
              sgBox.style.display = 'none';
              const la = +r.lat, lo = +r.lon;
              if (isSrc) { placeStart(la, lo, short); map.setView([la, lo], 15, { animate: true }); }
              else        { placeDest(la, lo, short);  map.setView([la, lo], 15, { animate: true }); }
            });
            sgBox.appendChild(el);
          });
        }
        sgBox.style.display = 'block';
      } catch (e) { console.warn('Geocode error:', e); }
    }, 400);
  });

  inp.addEventListener('blur',  () => setTimeout(() => { sgBox.style.display = 'none'; }, 200));
  inp.addEventListener('focus', () => { if (sgBox.children.length) sgBox.style.display = 'block'; });
}

setupAutocomplete('src-in', 'sg-src', true);
setupAutocomplete('dst-in', 'sg-dst', false);

/* ══════════════════════════════════════════════════════════════════
   10. SLIDER RESET
══════════════════════════════════════════════════════════════════ */
function resetSliders() {
  document.getElementById('asl').value = 5;
  document.getElementById('bsl').value = 5;
  document.getElementById('av').textContent = '5';
  document.getElementById('bv').textContent = '5';
}

/* ══════════════════════════════════════════════════════════════════
   11. CALCULATE ROUTES
══════════════════════════════════════════════════════════════════ */
async function calculate() {
  if (!srcCoord || !dstCoord) {
    setStatus('⚠️ Set both a start and destination first', 'err');
    toast('Pick start & destination!');
    return;
  }
  clearRoutes();
  setLoading(true);
  setStatus('⏳ Computing safest & fastest routes simultaneously…', 'info');

  const alpha = parseFloat(document.getElementById('asl').value);
  const beta  = parseFloat(document.getElementById('bsl').value);

  const [fastResult, safeResult] = await Promise.allSettled([
    fetchFastest(),
    fetchSafest(alpha, beta),
  ]);

  let anyOk = false;

  // ── Fastest route (OSRM) — Blue
  if (fastResult.status === 'fulfilled' && fastResult.value) {
    const { coords, dist, dur } = fastResult.value;
    fastPolyline = L.polyline(coords, { color: '#3b82f6', weight: 6, opacity: .85 }).addTo(map);
    fastPolyline.on('click', () => toast('🔵 Fastest Route — time-optimised via OSRM/OpenStreetMap'));
    document.getElementById('fast-time').textContent = formatTime(dur);
    document.getElementById('fast-dist').textContent = formatDist(dist);
    anyOk = true;
  } else {
    document.getElementById('fast-time').textContent = 'Offline';
    document.getElementById('fast-dist').textContent = '—';
  }

  // ── Safest route (Python backend) — Green
  if (safeResult.status === 'fulfilled' && safeResult.value) {
    const { route, metrics } = safeResult.value;
    safePolyline = L.polyline(
      route.map(p => [p[0], p[1]]),
      { color: '#22d3a0', weight: 7, opacity: .95 }
    ).addTo(map);
    safePolyline.on('click', () => toast('🟢 Safest Route — crime & infrastructure weighted Dijkstra'));

    const score = metrics.safety_rating;
    document.getElementById('safe-score').textContent = score;
    document.getElementById('safe-dist').textContent  = `${metrics.distance_km} km`;
    document.getElementById('safe-crime').textContent = `${metrics.avg_crime_score}/10`;
    document.getElementById('safe-infra').textContent = `${metrics.avg_infra_risk_score}/10`;

    // Animate safety ring
    const circ = 2 * Math.PI * 24;   // r=24
    const fill = (score / 10) * circ;
    document.getElementById('ring-prog').setAttribute('stroke-dasharray', `${fill} ${circ - fill}`);
    anyOk = true;
  } else {
    document.getElementById('safe-score').textContent = '—';
    document.getElementById('safe-dist').textContent  = 'Engine warming…';
    document.getElementById('safe-crime').textContent = '—';
    document.getElementById('safe-infra').textContent = '—';
  }

  setLoading(false);

  if (anyOk) {
    document.getElementById('results-section').style.display = 'block';
    fitRouteBounds();
    const bothOk = fastResult.status === 'fulfilled' && safeResult.status === 'fulfilled';
    if (bothOk) {
      setStatus('✅ Both routes drawn! 🟢 Green = Safest  ·  🔵 Blue = Fastest', 'ok');
    } else if (safeResult.status === 'fulfilled') {
      setStatus('✅ Safest route drawn. OSRM is currently offline.', 'ok');
    } else {
      setStatus('✅ Fastest route drawn. Backend engine warming up…', 'ok');
    }
  } else {
    setStatus('❌ Could not compute routes. Check your connection.', 'err');
  }
}

/* ──────────────────────────────────────────────────────────────────
   Fetch fastest (OSRM — free, no API key)
────────────────────────────────────────────────────────────────── */
async function fetchFastest() {
  const url = `${OSRM_URL}/${srcCoord.lng},${srcCoord.lat};${dstCoord.lng},${dstCoord.lat}`
    + `?overview=full&geometries=geojson`;
  const res = await fetch(url, { signal: AbortSignal.timeout(15000) });
  if (!res.ok) throw new Error(`OSRM HTTP ${res.status}`);
  const data = await res.json();
  if (data.code !== 'Ok' || !data.routes?.length) throw new Error('No OSRM route');
  const r = data.routes[0];
  return {
    coords: r.geometry.coordinates.map(c => [c[1], c[0]]),
    dist:   r.distance,
    dur:    r.duration,
  };
}

/* ──────────────────────────────────────────────────────────────────
   Fetch safest (Python/NetworkX backend)
────────────────────────────────────────────────────────────────── */
async function fetchSafest(alpha, beta) {
  const url = `/api/route`
    + `?start_lat=${srcCoord.lat}&start_lng=${srcCoord.lng}`
    + `&end_lat=${dstCoord.lat}&end_lng=${dstCoord.lng}`
    + `&alpha=${alpha}&beta=${beta}`;
  const res = await fetch(url, { signal: AbortSignal.timeout(45000) });
  if (!res.ok) throw new Error(await res.text());
  const data = await res.json();
  if (data.status !== 'success') throw new Error('API returned non-success');
  return { route: data.route, metrics: data.metrics };
}

/* ──────────────────────────────────────────────────────────────────
   Helpers
────────────────────────────────────────────────────────────────── */
function clearRoutes() {
  if (safePolyline) { safePolyline.remove(); safePolyline = null; }
  if (fastPolyline) { fastPolyline.remove(); fastPolyline = null; }
  document.getElementById('results-section').style.display = 'none';
}

function fitRouteBounds() {
  const pts = [];
  if (safePolyline) pts.push(...safePolyline.getLatLngs());
  if (fastPolyline) pts.push(...fastPolyline.getLatLngs());
  if (pts.length) map.fitBounds(L.latLngBounds(pts), { padding: [60, 60] });
}

function formatTime(s) {
  const m = Math.round(s / 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)}h ${m % 60}m`;
}
function formatDist(m) {
  return m < 1000 ? `${Math.round(m)} m` : `${(m / 1000).toFixed(1)} km`;
}

/* ══════════════════════════════════════════════════════════════════
   12. UI HELPERS
══════════════════════════════════════════════════════════════════ */
function setLoading(on) {
  const btn = document.getElementById('calc-btn');
  document.getElementById('sp').style.display    = on ? 'block' : 'none';
  document.getElementById('btn-ic').style.display = on ? 'none'  : 'block';
  document.getElementById('btn-lbl').textContent  = on ? 'Calculating…' : 'Calculate Safe Route';
  btn.disabled = on;
}

function setStatus(msg, cls = '') {
  const el = document.getElementById('status-msg');
  el.textContent = msg;
  el.className   = cls;
}

let toastTimer;
function toast(msg) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 2800);
}

/* ══════════════════════════════════════════════════════════════════
   13. LAYER TOGGLES
══════════════════════════════════════════════════════════════════ */
function toggleLayer(name) {
  layerOn[name] = !layerOn[name];
  document.getElementById(`lp-${name}`).classList.toggle('active', layerOn[name]);
  layers[name].forEach(m => layerOn[name] ? m.addTo(map) : m.remove());
}

/* ══════════════════════════════════════════════════════════════════
   14. LOAD OVERLAY DATA
══════════════════════════════════════════════════════════════════ */
async function loadInfrastructure() {
  try {
    const res  = await fetch('/api/infrastructure', { signal: AbortSignal.timeout(10000) });
    if (!res.ok) return;
    const { positive, negative, safetipin } = await res.json();

    positive.forEach(p => {
      const icon = p.type === 'police' ? '🚔' : p.type === 'metro_station' ? '🚉'
                 : p.type === 'she_team' ? '💪' : '🏢';
      const m = L.marker([p.lat, p.lng], { icon: dotIcon('#22d3a0', 9) })
        .bindPopup(`<div class="pop-title">${icon} ${p.name}</div><div class="pop-sub" style="text-transform:capitalize">${(p.type||'').replace(/_/g,' ')}</div>`);
      if (layerOn.safe) m.addTo(map);
      layers.safe.push(m);
    });

    negative.forEach(p => {
      const m = L.marker([p.lat, p.lng], { icon: dotIcon('#f43f5e', 9) })
        .bindPopup(`<div class="pop-title">⚠️ ${p.name}</div><div class="pop-sub" style="color:#f87171">High-risk / isolated area</div>`);
      if (layerOn.risk) m.addTo(map);
      layers.risk.push(m);
    });

    (safetipin || []).slice(0, 80).forEach(p => {
      const s = p.safety_score;
      const c = s >= 7 ? '#22d3a0' : s >= 4 ? '#f59e0b' : '#f43f5e';
      const m = L.circleMarker([p.lat, p.lng], {
        radius: 5, color: c, fillColor: c, fillOpacity: .7, weight: 0,
      }).bindPopup(`<div class="pop-title">📊 SafetiPin Audit</div><div class="pop-sub">Score: <b style="color:${c}">${s}/10</b></div>`);
      if (layerOn.audit) m.addTo(map);
      layers.audit.push(m);
    });
  } catch (e) { console.warn('Infrastructure overlay error:', e.message); }
}

async function loadCrimeZones() {
  try {
    const res  = await fetch('/api/crime-zones', { signal: AbortSignal.timeout(10000) });
    if (!res.ok) return;
    const { zones } = await res.json();

    zones.forEach(z => {
      const s   = z.crime_score;
      const col = s < 3 ? '#22d3a0' : s < 5 ? '#f59e0b' : s < 7 ? '#f97316' : '#f43f5e';
      const c = L.circle([z.lat, z.lng], {
        radius:      700 + s * 130,
        color:       col,
        fillColor:   col,
        fillOpacity: 0.07 + s * 0.013,
        weight:      0,
      }).bindPopup(`<div class="pop-title">🗺 ${z.name}</div><div class="pop-sub">Crime score: <b style="color:${col}">${s}/10</b></div>`);
      if (layerOn.crime) c.addTo(map);
      layers.crime.push(c);
    });
  } catch (e) { console.warn('Crime zones error:', e.message); }
}

/* ══════════════════════════════════════════════════════════════════
   15. HEALTH CHECK (polls until engine is ready)
══════════════════════════════════════════════════════════════════ */
async function checkHealth() {
  const dot = document.getElementById('engine-dot');
  const lbl = document.getElementById('engine-lbl');
  try {
    const res  = await fetch('/api/health', { signal: AbortSignal.timeout(5000) });
    const data = await res.json();
    if (data.engine_ready) {
      dot.className    = 'ok';
      lbl.textContent  = 'Engine Ready ✓';
      setStatus('Engine ready! Pick locations to calculate routes.', 'ok');
    } else {
      dot.className    = '';
      lbl.textContent  = 'Warming Up…';
      setStatus('⏳ Safety engine is loading Hyderabad graph…', 'info');
      setTimeout(checkHealth, 5000);
    }
  } catch {
    dot.className    = 'err';
    lbl.textContent  = 'Backend Offline';
    setStatus('⚠️ Backend offline — run: python backend/main.py', 'err');
    setTimeout(checkHealth, 8000);
  }
}

/* ══════════════════════════════════════════════════════════════════
   16. SHARE LINK
══════════════════════════════════════════════════════════════════ */
function copyLink() {
  if (!srcCoord || !dstCoord) return;
  const a = document.getElementById('asl').value;
  const b = document.getElementById('bsl').value;
  const link = `${location.origin}${location.pathname}`
    + `?slat=${srcCoord.lat}&slng=${srcCoord.lng}`
    + `&dlat=${dstCoord.lat}&dlng=${dstCoord.lng}`
    + `&alpha=${a}&beta=${b}`;
  navigator.clipboard.writeText(link)
    .then(() => toast('🔗 Route link copied!'))
    .catch(() => toast('Could not copy — please copy manually'));
}

/* ══════════════════════════════════════════════════════════════════
   17. DEEP LINK — load route from URL params
══════════════════════════════════════════════════════════════════ */
function loadDeepLink() {
  const p = new URLSearchParams(location.search);
  if (!p.get('slat') || !p.get('dlat')) return;
  const slat = +p.get('slat'), slng = +p.get('slng');
  const dlat = +p.get('dlat'), dlng = +p.get('dlng');
  if (!TELANGANA_BOUNDS.contains([slat, slng]) || !TELANGANA_BOUNDS.contains([dlat, dlng])) return;
  placeStart(slat, slng, `${slat.toFixed(5)}, ${slng.toFixed(5)}`);
  placeDest(dlat, dlng,  `${dlat.toFixed(5)}, ${dlng.toFixed(5)}`);
  if (p.get('alpha')) {
    document.getElementById('asl').value = p.get('alpha');
    document.getElementById('av').textContent = parseFloat(p.get('alpha'));
  }
  if (p.get('beta')) {
    document.getElementById('bsl').value = p.get('beta');
    document.getElementById('bv').textContent = parseFloat(p.get('beta'));
  }
  setTimeout(calculate, 1200);
}

/* ══════════════════════════════════════════════════════════════════
   18. BOOT
══════════════════════════════════════════════════════════════════ */
checkHealth();
loadInfrastructure();
loadCrimeZones();
loadDeepLink();
