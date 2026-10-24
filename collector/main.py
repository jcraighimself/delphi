"""Collector: read the indoor sensors and send the readings to the Delphi server.

    python collector/main.py                             # every 5 minutes: read and send
    python collector/main.py --once --print              # one reading, printed and sent
    python collector/main.py --once --print --no-send    # just read the sensors

The server address and API key come from a .env file in the project folder
(copy .env.example to .env and fill it in).
"""

import argparse
import logging
import os
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

from sensors import FIELDS, Sensors
from uploader import Uploader

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def print_reading(reading):
    print(time.strftime("%Y-%m-%d %H:%M:%S"))
    for field in FIELDS:
        value = reading[field]
        print(f"  {field:<22} {'—' if value is None else value}")


def make_uploader():
    load_dotenv(os.path.join(PROJECT_DIR, ".env"))
    server_url = os.environ.get("DELPHI_SERVER_URL")
    api_key = os.environ.get("DELPHI_API_KEY")
    if not server_url or not api_key:
        raise SystemExit("Set DELPHI_SERVER_URL and DELPHI_API_KEY in .env (or use --no-send).")
    return Uploader(server_url, api_key)


def main():
    parser = argparse.ArgumentParser(description="Read Delphi's indoor sensors and send them to the server.")
    parser.add_argument("--once", action="store_true", help="take one reading and exit")
    parser.add_argument("--print", action="store_true", help="print each reading")
    parser.add_argument("--no-send", action="store_true", help="don't send readings to the server")
    parser.add_argument("--interval", type=float, default=300, help="seconds between readings (default 300)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    uploader = None if args.no_send else make_uploader()
    sensors = Sensors()
    try:
        while True:
            reading = sensors.read()
            reading["recorded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            if args.print:
                print_reading(reading)
            else:
                logging.info("reading: %s", reading)
            if uploader is not None:
                uploader.send(reading)
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        sensors.close()


if __name__ == "__main__":
    main()
