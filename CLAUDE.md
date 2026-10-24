# Delphi

An "oracle" for the room: a Raspberry Pi 4 that reads indoor air conditions and reports them to an always-on online dashboard. Later, an e-ink display in the room shows weather, indoor air, gaming news/deals, and album art for whatever song is playing nearby.

Owner is a beginner-to-intermediate maker learning as they go. Explain non-obvious hardware/Linux steps briefly, prefer simple readable Python over clever code, and keep changes small enough to test one at a time.

## Status

- **Phase 1 (current): online dashboard.** The Pi reads its sensors and sends a reading to a server on Railway every 5 minutes. A password-protected website (only the owner can see it) shows the latest readings, charts of history, and monthly maintenance reminders. Sensor data only to start; weather, gaming, and music may be added to the site later.
- **Phase 2 (later): e-ink display in the room.** Add a 13.3" Spectra 6 e-ink display (Pimoroni Inky Impression 13.3" PIM774, or Waveshare 13.3" e-Paper HAT+ (E) as fallback) and an A4 shadow-box frame, showing weather, indoor air, gaming, and now-playing. The online dashboard keeps running alongside it.

In Phase 2, display output must stay behind a single interface so switching between PNG preview, Inky, and Waveshare is a config change, not a rewrite.

## Hardware

| Part | Notes |
|---|---|
| Raspberry Pi 4 Model B, 2GB | Hostname `delphi`, reached as `delphi.local` over SSH. Headless, Raspberry Pi OS (64-bit, Lite or Desktop). |
| Power | Official-spec 5.1V 3A USB-C supply |
| Storage | SanDisk Extreme 32GB microSD |
| BME688 (Pimoroni breakout) | Temp, humidity, pressure, gas resistance (air quality). I2C address **0x76**. Primary thermometer. |
| Adafruit SCD-41 (STEMMA QT) | True CO2 (ppm) + temp/humidity. I2C address **0x62**. Its temp reads warm; use the BME688 for temperature. |
| USB mini microphone | Plug-and-play, shows up as an ALSA capture device. Used for song recognition. |
| Display (Phase 2) | 1600×1200, 6 colours, full refresh ~20–35 s real-world. Not a touchscreen. |

### Wiring (Phase 1)

Qwiic/STEMMA QT/Qw/ST chain on I2C bus 1:

```
Pi GPIO header ──(20 cm Qwiic-to-female jumper)──▶ BME688 ──(10 cm Qwiic)──▶ SCD-41
```

| Wire colour | Signal | Pi physical pin |
|---|---|---|
| Red | 3.3V | 1 |
| Blue | SDA | 3 |
| Yellow | SCL | 5 |
| Black | GND | 6 |

- I2C must be enabled (`sudo raspi-config` → Interface Options → I2C). Verify with `i2cdetect -y 1`; expect `0x62` and `0x76`.
- Keep total I2C cable length under ~1 m. If a sensor disappears or reads garbage, suspect the cable first.
- Keep the BME688 a few cm from the Pi (heat skews readings). The SCD-41 needs airflow.
- In Phase 2 the chain moves to the display's Qw/ST socket (Inky) or stays on the jumper (Waveshare).

## E-ink display constraints (Phase 2; design for these)

- Canvas: **1600 × 1200** landscape (4:3).
- Palette is exactly six colours: black, white, red, yellow, green, blue. Everything else is dithered.
  - Text, icons, and UI elements: use solid palette colours only. No anti-aliased grey text, no gradients, no thin pastel lines.
  - Photos/album art: dither to the 6-colour palette and accept a screen-printed look.
- Refresh is slow and flashes. Only push a new frame when content actually changes. Never animate. Target updates every 5–15 minutes, or on song change.
- The preview pipeline must produce **two** images: `latest.png` (full colour, for layout work) and `latest_eink.png` (quantised + dithered to the 6-colour palette) so designs are judged as they will really look.

## Software stack

- **Python 3** in a virtual environment (Raspberry Pi OS Bookworm blocks system-wide pip installs). Create with `python3 -m venv .venv` and activate before installing.
- **requests** for HTTP; **Pillow** for drawing the e-ink frame (Phase 2).
- Sensors: `bme680` package (Pimoroni; supports the BME688) for the BME688. The SCD-41 is driven directly over I2C with `smbus2` (a small driver in `collector/sensors.py`), avoiding Adafruit's Blinka and its GPIO dependencies. Confirm current package names before installing.
- Display (Phase 2): Pimoroni `inky` library, or Waveshare's Python driver.
- Audio: `sounddevice` (or `arecord`) to capture; `shazamio` for recognition (unofficial, can break), with ACRCloud or AudD as fallback. Spotify's "currently playing" API is an optional primary source.
- Weather: Open-Meteo (free, no API key).
- The collector runs as a **systemd service** so it starts on boot and restarts on crash.
- **Server:** a small **Python** web app using **Flask**, run with **gunicorn**, on **Railway** (Hobby plan, shared with three other projects). Readings are stored in **SQLite on a Railway volume**, not a separate Postgres service, to keep usage low.

## Architecture

### Phase 1: online dashboard

```
Pi (collector)                         Railway (server)                      Owner
read sensors ──POST every 5 min──▶  web app ──▶ SQLite on a volume  ◀── browser, password login
+ secret API key                     receives readings, serves dashboard
```

```
delphi/
  CLAUDE.md
  requirements.txt       # Pi-side Python packages
  collector/             # runs on the Pi
    main.py              # every 5 min: read sensors → send to server (queue if offline)
    sensors.py           # BME688 + SCD-41; returns None per field on failure
    uploader.py          # POSTs readings; local queue.db while the server is unreachable
  server/                # runs on Railway
                         # receives readings, stores them, serves the dashboard
```

- **Upload:** the collector POSTs each reading as JSON to the server with a secret API key in a header. The key lives in `.env` on the Pi and as a Railway variable; it is never committed.
- **Timestamps** are set by the Pi, in UTC.
- **Offline queue:** if a send fails, the reading is saved to a small local SQLite file (git-ignored by `*.db`) and resent oldest-first later, so charts have no gaps. It's written to only when sending fails, to spare the SD card.
- **Dashboard:** a single password login; latest values (shown as "—" when missing), charts for the last day/week/month, and a warning if no reading has arrived for more than 15 minutes.
- **Maintenance reminders:** the Pi includes its last boot time and last `apt` upgrade time (from `/var/log/apt/history.log`) with its uploads. When either is more than ~30 days old, the dashboard shows a banner reminding the owner to update (`sudo apt update && sudo apt full-upgrade`) and/or restart (`sudo reboot`). Dashboard only; no email or push notifications for now.

**Expected cost:** the server should use about $2/month of Railway usage (~150 MB RAM, near-idle CPU, 1 GB volume; readings take ~10–20 MB/year). Hobby includes $5/month of usage, shared with the other three projects, so the real cost is whatever pushes the total past that credit: at most ~$2/month extra.

### Phase 2: e-ink display

```
delphi/
  config.toml            # location, API keys, update intervals, display backend
  main.py                # main loop: gather → render → output (only if changed)
  sources/               # one module per data source, each returns plain data
    weather.py
    sensors.py           # reuses collector/sensors.py
    music.py             # mic capture + recognition, runs in background thread
    gaming.py            # free games / news feeds
  render/
    layout.py            # draws the dashboard frame with Pillow
    music_mode.py        # album art frame
    palette.py           # the 6 colours + quantise/dither helper
  output/
    png.py               # Phase 1: writes latest.png + latest_eink.png
    inky.py              # Phase 2
    waveshare.py         # Phase 2 fallback
  assets/                # fonts, icons
  preview/               # tiny web page that auto-refreshes latest_eink.png
```

Principles:
- **Sources never draw; renderers never fetch.** Sources return dicts/dataclasses; renderers take data and return a PIL `Image`.
- **Every source must fail soft.** Network down or sensor missing → return last good value or `None`, and the renderer shows "—" instead of crashing.
- **Cache network calls** and respect rate limits (weather ≤ every 10 min, gaming feeds ≤ hourly).
- **Fake mode:** `--fake` flag swaps in sample data for all sources, so the layout can be developed on a normal PC with no Pi or sensors attached.
- Secrets (API keys, Spotify tokens) live in `config.toml` or `.env`, which are git-ignored. Never commit them.

## E-ink modes (Phase 2)

1. **Dashboard** (default): weather, indoor conditions with CO2 "open a window" warning (>1000 ppm), gaming panel, clock/date.
2. **Now playing:** when music is detected and identified, show album art + title/artist. Return to dashboard after a few minutes of silence.
3. Phase 2: the Inky's four buttons switch modes, force refresh, and mute the mic.

## Privacy

The mic is always listening for music. Keep clips short (~5–10 s), process in memory, **never write audio to disk or log it**, and provide a mute toggle.

## Dev workflow

- Code lives on GitHub (`jcraighimself/delphi`). Edit on the PC, push, then `git pull` on the Pi. The Pi's clone is at `~/delphi` (user `delphiadmin`). VS Code **Remote – SSH** to `delphi.local` is an alternative for editing directly on the Pi.
- Run the collector manually on the Pi: `cd ~/delphi && source .venv/bin/activate && python collector/main.py --once --print`.
- Server: deployed to Railway from the `server/` folder of the GitHub repo.
- Phase 2: develop the e-ink layout locally with `--fake`, and preview at `http://delphi.local:8000` (or the local preview server).
- Useful checks: `i2cdetect -y 1` (sensors), `arecord -l` (mic), `vcgencmd get_throttled` (power problems; anything other than `0x0` means a power or heat issue).

## Roadmap

Phase 1: online dashboard
1. ✅ Read both sensors and print values (`collector/main.py --once --print`).
2. Server on Railway: receive readings (API key) and store them in SQLite.
3. Collector sends a reading every 5 minutes, with the offline queue.
4. Dashboard website: password login, latest values, day/week/month charts, stale-data warning.
5. Monthly update/restart reminders on the dashboard.
6. Collector as a systemd service, logging, read-only-friendly setup to protect the SD card.

Phase 2: e-ink display
7. Fake-data dashboard rendering to PNG + e-ink simulation, with browser preview.
8. Live weather + live sensors on the e-ink dashboard.
9. Song recognition and now-playing mode.
10. Gaming panel (free games of the week, news).
11. Display backend, buttons, frame.

Later ideas: calendar, transit departures, Twitch live list, Steam wishlist deals, museum art of the day, wireless room sensors (ESP32 or BLE thermometers).
