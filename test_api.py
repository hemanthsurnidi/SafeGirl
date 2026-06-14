import requests, json

base = 'http://localhost:8000'

print('=== 1. Health Check ===')
r = requests.get(base + '/api/health')
print(json.dumps(r.json(), indent=2))

print('\n=== 2. Crime Zones ===')
r = requests.get(base + '/api/crime-zones')
z = r.json()['zones']
print('Total zones:', len(z))
print('Sample:', z[0])

print('\n=== 3. Infrastructure ===')
r = requests.get(base + '/api/infrastructure')
d = r.json()
pos = len(d['positive'])
neg = len(d['negative'])
sp  = len(d['safetipin'])
print('Positive:', pos, '| Negative:', neg, '| SafetiPin:', sp)

print('\n=== 3b. Autocomplete Proxy: "Warangal" ===')
r = requests.get(base + '/api/autocomplete', params={'q': 'Warangal'})
print('HTTP Status:', r.status_code)
if r.status_code == 200:
    results = r.json()
    print('Total results:', len(results))
    if results:
        print('First suggestion:', results[0]['display_name'], '| Lat/Lon:', results[0]['lat'], results[0]['lon'])
else:
    print('Error:', r.text[:300])

print('\n=== 4. Safest Route: Madhapur -> Charminar ===')
r = requests.get(base + '/api/route', params={
    'start_lat': 17.4485, 'start_lng': 78.3741,
    'end_lat':   17.3616, 'end_lng':   78.4747,
    'alpha': 5, 'beta': 5
}, timeout=30)
print('HTTP Status:', r.status_code)
if r.status_code == 200:
    d = r.json()
    print('Safest Metrics:', json.dumps(d['safest']['metrics'], indent=2))
    print('Safest Route points:', len(d['safest']['route']))
    print('Safest First coord:', d['safest']['route'][0])
    print('Safest Last coord:', d['safest']['route'][-1])
    print('Balanced Metrics:', json.dumps(d['balanced']['metrics'], indent=2))
    print('Fastest Metrics:', json.dumps(d['fastest']['metrics'], indent=2))
else:
    print('Error:', r.text[:300])

print('\n=== 5. Safest Route: Gachibowli -> Secunderabad ===')
r = requests.get(base + '/api/route', params={
    'start_lat': 17.4172, 'start_lng': 78.3569,
    'end_lat':   17.4502, 'end_lng':   78.5029,
    'alpha': 5, 'beta': 5
}, timeout=30)
print('HTTP Status:', r.status_code)
if r.status_code == 200:
    d = r.json()
    print('Safest Metrics:', json.dumps(d['safest']['metrics'], indent=2))
print('\n=== 5b. Telangana Route (Inter-city): Warangal -> Charminar (Hyd) ===')
r = requests.get(base + '/api/route', params={
    'start_lat': 17.9784, 'start_lng': 79.5941,
    'end_lat':   17.3616, 'end_lng':   78.4747,
    'alpha': 5, 'beta': 5
}, timeout=30)
print('HTTP Status:', r.status_code)
if r.status_code == 200:
    d = r.json()
    print('Safest Metrics:', json.dumps(d['safest']['metrics'], indent=2))
    print('Safest Route points:', len(d['safest']['route']))
else:
    print('Error:', r.text[:300])

print('\n=== 5c. Telangana Route (Both outside Hyd): Karimnagar -> Warangal ===')
r = requests.get(base + '/api/route', params={
    'start_lat': 18.4386, 'start_lng': 79.1288,
    'end_lat':   17.9784, 'end_lng':   79.5941,
    'alpha': 5, 'beta': 5
}, timeout=30)
print('HTTP Status:', r.status_code)
if r.status_code == 200:
    d = r.json()
    print('Safest Metrics:', json.dumps(d['safest']['metrics'], indent=2))
    print('Safest Route points:', len(d['safest']['route']))
else:
    print('Error:', r.text[:300])

print('\n=== 6. Frontend index.html ===')
r = requests.get(base + '/')
print('HTTP Status:', r.status_code)
print('Has Leaflet:', 'leaflet' in r.text.lower())
print('Has Google Maps:', 'maps.googleapis.com' in r.text)
print('Content length:', len(r.text), 'bytes')

print('\n=== 7. app.js ===')
r = requests.get(base + '/app.js')
print('HTTP Status:', r.status_code, '| Size:', len(r.text), 'bytes')
print('Has OSRM:', 'project-osrm.org' in r.text)
print('Has Google Maps:', 'googleapis' in r.text)

print('\n=== 8. styles.css ===')
r = requests.get(base + '/styles.css')
print('HTTP Status:', r.status_code, '| Size:', len(r.text), 'bytes')

print('\n=== ALL TESTS COMPLETE ===')
