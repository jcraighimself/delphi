"""Delphi server: receives sensor readings from the Pi and shows the latest one.

Settings come from environment variables (on Railway, set them as service variables):
    DELPHI_API_KEY      secret the Pi sends with every reading (required)
    DASHBOARD_PASSWORD  password for the web page (required)
    DATABASE_PATH       where the SQLite file lives (default: delphi.db)

Run locally with:  python main.py   (then open http://127.0.0.1:5000)
"""

import hmac
import os
import sqlite3
from datetime import datetime, timezone

from flask import Flask, Response, abort, g, jsonify, render_template_string, request


def required_env(name):
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Set the {name} environment variable before starting the server.")
    return value


API_KEY = required_env("DELPHI_API_KEY")
DASHBOARD_PASSWORD = required_env("DASHBOARD_PASSWORD")
DATABASE_PATH = os.environ.get("DATABASE_PATH", "delphi.db")

# Must match FIELDS in collector/sensors.py.
FIELDS = (
    "temperature_c",
    "humidity_pct",
    "pressure_hpa",
    "gas_ohms",
    "co2_ppm",
    "scd41_temperature_c",
    "scd41_humidity_pct",
)

app = Flask(__name__)


# --- Database ---------------------------------------------------------------

def init_db():
    # NUMERIC keeps whole numbers (like CO2) as integers and decimals as decimals.
    columns = ", ".join(f"{field} NUMERIC" for field in FIELDS)
    db = sqlite3.connect(DATABASE_PATH)
    db.execute(
        "CREATE TABLE IF NOT EXISTS readings ("
        " recorded_at TEXT PRIMARY KEY,"  # when the Pi took the reading (UTC)
        " received_at TEXT NOT NULL,"     # when the server got it (UTC)
        f" {columns})"
    )
    db.commit()
    db.close()


def get_db():
    """One database connection per request, closed automatically afterwards."""
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- Receiving readings from the Pi ------------------------------------------

def parse_reading(reading):
    """Check one reading from the Pi and turn it into a database row.

    Raises ValueError with a helpful message if the reading is malformed.
    """
    if not isinstance(reading, dict):
        raise ValueError("each reading must be a JSON object")
    try:
        recorded_at = datetime.fromisoformat(reading["recorded_at"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("recorded_at must be a timestamp like 2026-10-24T16:23:03Z") from None
    if recorded_at.tzinfo is None:
        raise ValueError("recorded_at must include a timezone (use UTC, ending in Z)")

    row = [recorded_at.astimezone(timezone.utc).isoformat(timespec="seconds"), utc_now()]
    for field in FIELDS:
        value = reading.get(field)
        # None means the sensor couldn't be read; anything else must be a number.
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise ValueError(f"{field} must be a number or null")
        row.append(value)
    return row


@app.post("/api/readings")
def add_readings():
    """Accept one reading (a JSON object) or several (a JSON list of them)."""
    key = request.headers.get("X-API-Key", "")
    if not hmac.compare_digest(key.encode(), API_KEY.encode()):
        abort(401)

    body = request.get_json(silent=True)
    readings = body if isinstance(body, list) else [body]
    try:
        rows = [parse_reading(reading) for reading in readings]
    except ValueError as error:
        return jsonify(error=str(error)), 400

    columns = ("recorded_at", "received_at") + FIELDS
    placeholders = ", ".join("?" for _ in columns)
    db = get_db()
    # OR IGNORE: if the Pi resends a reading we already have, skip it instead of failing.
    cursor = db.executemany(
        f"INSERT OR IGNORE INTO readings ({', '.join(columns)}) VALUES ({placeholders})", rows
    )
    db.commit()
    return jsonify(received=len(rows), stored=cursor.rowcount), 201


# --- Web page -----------------------------------------------------------------

PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Delphi</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 32rem; margin: 2rem auto; padding: 0 1rem; }
    td { padding: 0.3rem 1rem 0.3rem 0; }
    td:last-child { font-weight: bold; text-align: right; }
    .muted { color: #666; }
  </style>
</head>
<body>
  <h1>Delphi</h1>
  {% if latest %}
    <p class="muted">Last reading: {{ latest["recorded_at"] }} (UTC)</p>
    <table>
      {% for field in fields %}
        <tr>
          <td>{{ field }}</td>
          <td>{{ "—" if latest[field] is none else latest[field] }}</td>
        </tr>
      {% endfor %}
    </table>
  {% else %}
    <p>No readings yet.</p>
  {% endif %}
</body>
</html>
"""


@app.get("/")
def dashboard():
    # Browser login box (HTTP Basic auth). Any username works; only the password is checked.
    auth = request.authorization
    password = (auth.password or "") if auth else ""
    if not hmac.compare_digest(password.encode(), DASHBOARD_PASSWORD.encode()):
        return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="Delphi"'})

    latest = get_db().execute(
        "SELECT * FROM readings ORDER BY recorded_at DESC LIMIT 1"
    ).fetchone()
    return render_template_string(PAGE, latest=latest, fields=FIELDS)


@app.get("/health")
def health():
    return "ok"


init_db()

if __name__ == "__main__":
    app.run(debug=True)
