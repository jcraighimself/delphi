"""Send readings to the Delphi server, keeping them in a local queue if that fails.

The queue is a small SQLite file next to this script. It only exists while
there are readings waiting to be sent, so the SD card isn't written to in
normal operation.
"""

import json
import logging
import os
import sqlite3

import requests

log = logging.getLogger(__name__)

QUEUE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "queue.db")
BATCH_SIZE = 100  # queued readings sent per request

# What happened to a POST:
SENT = "sent"          # the server stored it
RETRY = "retry"        # network or server trouble: keep it and try again later
REJECTED = "rejected"  # the server says the data itself is bad: retrying won't help


class Uploader:
    def __init__(self, server_url, api_key):
        self.url = server_url.rstrip("/") + "/api/readings"
        self.api_key = api_key

    def send(self, reading):
        """Send a reading, plus any queued ones. Returns True if nothing is left waiting."""
        if not os.path.exists(QUEUE_PATH):
            result = self._post([reading])
            if result == SENT:
                log.info("Sent reading to the server")
            if result != RETRY:
                return True
            self._add_to_queue(reading)
            return False
        # Older readings are waiting, so queue this one behind them to keep the order.
        self._add_to_queue(reading)
        return self._flush_queue()

    def _post(self, readings):
        try:
            response = requests.post(
                self.url,
                json=readings,
                headers={"X-API-Key": self.api_key},
                timeout=15,
            )
        except requests.RequestException as error:
            log.warning("Couldn't reach the server: %s", error)
            return RETRY
        if response.ok:
            return SENT
        if response.status_code == 400:
            log.error("Server rejected %d reading(s): %s", len(readings), response.text)
            return REJECTED
        if response.status_code == 401:
            log.error("Server refused the API key; check DELPHI_API_KEY in .env")
        else:
            log.warning("Server replied with HTTP %d", response.status_code)
        return RETRY

    def _add_to_queue(self, reading):
        db = sqlite3.connect(QUEUE_PATH)
        db.execute("CREATE TABLE IF NOT EXISTS queue (id INTEGER PRIMARY KEY, reading TEXT NOT NULL)")
        db.execute("INSERT INTO queue (reading) VALUES (?)", (json.dumps(reading),))
        db.commit()
        (count,) = db.execute("SELECT COUNT(*) FROM queue").fetchone()
        db.close()
        log.warning("Saved reading to the local queue (%d waiting)", count)

    def _flush_queue(self):
        """Send queued readings oldest-first. Returns True once the queue is empty."""
        db = sqlite3.connect(QUEUE_PATH)
        try:
            while True:
                rows = db.execute(
                    "SELECT id, reading FROM queue ORDER BY id LIMIT ?", (BATCH_SIZE,)
                ).fetchall()
                if not rows:
                    break
                if self._post([json.loads(reading) for _, reading in rows]) == RETRY:
                    return False
                db.execute("DELETE FROM queue WHERE id <= ?", (rows[-1][0],))
                db.commit()
        finally:
            db.close()
        os.remove(QUEUE_PATH)
        log.info("Sent all queued readings")
        return True
