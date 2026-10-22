"""Collector: read the indoor sensors.

    python collector/main.py --once --print    # one reading, printed
    python collector/main.py --print           # a reading every --interval seconds
"""

import argparse
import logging
import time

from sensors import FIELDS, Sensors


def print_reading(reading):
    print(time.strftime("%Y-%m-%d %H:%M:%S"))
    for field in FIELDS:
        value = reading[field]
        print(f"  {field:<22} {'—' if value is None else value}")


def main():
    parser = argparse.ArgumentParser(description="Read Delphi's indoor sensors.")
    parser.add_argument("--once", action="store_true", help="take one reading and exit")
    parser.add_argument("--print", action="store_true", help="print each reading")
    parser.add_argument("--interval", type=float, default=60, help="seconds between readings (default 60)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    sensors = Sensors()
    try:
        while True:
            reading = sensors.read()
            if args.print:
                print_reading(reading)
            else:
                logging.info("reading: %s", reading)
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        sensors.close()


if __name__ == "__main__":
    main()
