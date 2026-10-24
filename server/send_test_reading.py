"""Send one fake reading to the server, to test it without the Pi.

    python send_test_reading.py http://127.0.0.1:5000 <api-key>
"""

import json
import random
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

server_url, api_key = sys.argv[1], sys.argv[2]

reading = {
    "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "temperature_c": round(random.uniform(19, 24), 1),
    "humidity_pct": round(random.uniform(35, 55), 1),
    "pressure_hpa": round(random.uniform(995, 1025), 1),
    "gas_ohms": None,
    "co2_ppm": random.randint(450, 1200),
    "scd41_temperature_c": round(random.uniform(20, 25), 1),
    "scd41_humidity_pct": round(random.uniform(35, 55), 1),
}

request = urllib.request.Request(
    server_url.rstrip("/") + "/api/readings",
    data=json.dumps(reading).encode(),
    headers={"Content-Type": "application/json", "X-API-Key": api_key},
    method="POST",
)
try:
    with urllib.request.urlopen(request) as response:
        print(response.status, response.read().decode())
except urllib.error.HTTPError as error:
    print(error.code, error.read().decode())
