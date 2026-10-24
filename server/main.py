"""Delphi server: receives sensor readings from the Pi and shows them on a dashboard.

Settings come from environment variables (on Railway, set them as service variables):
    DELPHI_API_KEY      secret the Pi sends with every reading (required)
    DASHBOARD_PASSWORD  password for the dashboard (required)
    SECRET_KEY          random string used to sign login cookies (required)
    DATABASE_PATH       where the SQLite file lives (default: delphi.db)

Run locally with:  python main.py   (then open http://127.0.0.1:5000)
"""

import functools
import hmac
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Flask, abort, g, jsonify, redirect, render_template, request, session, url_for


def required_env(name):
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Set the {name} environment variable before starting the server.")
    return value


API_KEY = required_env("DELPHI_API_KEY")
DASHBOARD_PASSWORD = required_env("DASHBOARD_PASSWORD")
SECRET_KEY = required_env("SECRET_KEY")
DATABASE_PATH = os.environ.get("DATABASE_PATH", "delphi.db")

# The database stores UTC; the dashboard shows Eastern time (EST/EDT switch automatically).
LOCAL_TZ = ZoneInfo("America/New_York")
STALE_AFTER = timedelta(minutes=15)  # warn if no reading has arrived for this long

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

# What the dashboard shows, in order. `scale` converts the stored value to the shown unit.
METRICS = (
    {"field": "co2_ppm", "label": "CO₂", "unit": "ppm", "decimals": 0, "scale": 1},
    {"field": "temperature_c", "label": "Temperature", "unit": "°C", "decimals": 1, "scale": 1},
    {"field": "humidity_pct", "label": "Humidity", "unit": "%", "decimals": 0, "scale": 1},
    {"field": "pressure_hpa", "label": "Pressure", "unit": "hPa", "decimals": 0, "scale": 1},
    {"field": "gas_ohms", "label": "Gas resistance", "unit": "kΩ", "decimals": 0, "scale": 0.001},
)

CO2_FAIR = 800    # ppm; above this the air is getting stuffy
CO2_POOR = 1000   # ppm; above this, open a window

# Chart ranges: how far back to look, and how many seconds of readings to average per point.
RANGES = {
    "day": (timedelta(days=1), 5 * 60),
    "week": (timedelta(days=7), 30 * 60),
    "month": (timedelta(days=30), 2 * 60 * 60),
}

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config.update(
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),  # stay logged in for a month
    SESSION_COOKIE_SAMESITE="Lax",
    # Only send the login cookie over HTTPS on Railway (local testing uses plain HTTP).
    SESSION_COOKIE_SECURE="RAILWAY_ENVIRONMENT" in os.environ,
)


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


# --- Login ----------------------------------------------------------------------

def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        if hmac.compare_digest(password.encode(), DASHBOARD_PASSWORD.encode()):
            session.clear()
            session["logged_in"] = True
            session.permanent = True
            return redirect(url_for("dashboard"))
        time.sleep(1)  # slows down anyone trying to guess the password
        error = "Incorrect password. Try again."
    return render_template("login.html", error=error)


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# --- Dashboard --------------------------------------------------------------------

def format_local(moment):
    """A UTC datetime as Eastern time, e.g. 'Sat Oct 24, 3:42 PM EDT'."""
    local = moment.astimezone(LOCAL_TZ)
    hour = local.strftime("%I").lstrip("0")
    return f"{local:%a %b} {local.day}, {hour}:{local:%M %p %Z}"


def describe_age(age):
    minutes = int(age.total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    return f"{hours // 24} days ago"


def format_value(value, metric):
    if value is None:
        return "—"
    return f"{value * metric['scale']:,.{metric['decimals']}f}"


def co2_status(ppm):
    """(css class, label) for the CO2 tile."""
    if ppm is None:
        return None, None
    if ppm > CO2_POOR:
        return "poor", "Open a window"
    if ppm > CO2_FAIR:
        return "fair", "Getting stuffy"
    return "good", "Fresh"


def latest_tiles(latest):
    tiles = []
    for metric in METRICS:
        value = latest[metric["field"]] if latest else None
        status, note = co2_status(value) if metric["field"] == "co2_ppm" else (None, None)
        tiles.append({
            "label": metric["label"],
            "value": format_value(value, metric),
            "unit": metric["unit"],
            "status": status,
            "note": note,
        })
    return tiles


def axis_ticks(start, end, range_name):
    """Tick marks for a chart's time axis, on round Eastern times, with their labels."""
    ticks = []
    moment = start.astimezone(LOCAL_TZ).replace(minute=0, second=0, microsecond=0)
    while moment <= end:
        if moment >= start:
            hour = moment.strftime("%I").lstrip("0")
            if range_name == "day" and moment.hour % 3 == 0:
                label = f"{moment:%a}" if moment.hour == 0 else f"{hour} {moment:%p}"
                ticks.append({"value": int(moment.timestamp() * 1000), "label": label})
            elif range_name == "week" and moment.hour == 0:
                ticks.append({"value": int(moment.timestamp() * 1000), "label": f"{moment:%a} {moment.day}"})
            elif range_name == "month" and moment.hour == 0 and moment.toordinal() % 5 == 0:
                ticks.append({"value": int(moment.timestamp() * 1000), "label": f"{moment:%b} {moment.day}"})
        moment += timedelta(hours=1)
    return ticks


def chart_data(db, range_name):
    """Averaged readings for the charts, plus the time axis to draw them on."""
    span, bucket = RANGES[range_name]
    end = datetime.now(timezone.utc)
    start = end - span
    averages = ", ".join(f"AVG({metric['field']}) AS {metric['field']}" for metric in METRICS)
    rows = db.execute(
        f"SELECT CAST(strftime('%s', recorded_at) AS INTEGER) / ? * ? AS t, {averages}"
        " FROM readings WHERE recorded_at >= ? GROUP BY t ORDER BY t",
        (bucket, bucket, start.isoformat(timespec="seconds")),
    ).fetchall()

    times = []
    values = {metric["field"]: [] for metric in METRICS}
    previous = None
    for row in rows:
        # If the Pi was off for a while, add an empty point so the line breaks
        # instead of drawing a straight line across the gap.
        if previous is not None and row["t"] - previous > 3 * bucket:
            times.append((previous + bucket) * 1000)
            for metric in METRICS:
                values[metric["field"]].append(None)
        times.append(row["t"] * 1000)
        for metric in METRICS:
            value = row[metric["field"]]
            scaled = None if value is None else round(value * metric["scale"], metric["decimals"] + 1)
            values[metric["field"]].append(scaled)
        previous = row["t"]

    return {
        "start": int(start.timestamp() * 1000),
        "end": int(end.timestamp() * 1000),
        "ticks": axis_ticks(start, end, range_name),
        "times": times,
        "co2Poor": CO2_POOR,
        "metrics": [
            {
                "field": metric["field"],
                "label": metric["label"],
                "unit": metric["unit"],
                "decimals": metric["decimals"],
                "values": values[metric["field"]],
            }
            for metric in METRICS
        ],
    }


@app.get("/")
@login_required
def dashboard():
    range_name = request.args.get("range", "day")
    if range_name not in RANGES:
        range_name = "day"

    db = get_db()
    latest = db.execute("SELECT * FROM readings ORDER BY recorded_at DESC LIMIT 1").fetchone()
    last_seen = None
    stale = True
    if latest:
        recorded_at = datetime.fromisoformat(latest["recorded_at"])
        age = datetime.now(timezone.utc) - recorded_at
        last_seen = {"when": format_local(recorded_at), "ago": describe_age(age)}
        stale = age > STALE_AFTER

    return render_template(
        "dashboard.html",
        tiles=latest_tiles(latest),
        last_seen=last_seen,
        stale=stale,
        range_name=range_name,
        ranges=list(RANGES),
        charts=chart_data(db, range_name),
    )


@app.get("/health")
def health():
    return "ok"


init_db()

if __name__ == "__main__":
    app.run(debug=True)
