# Delphi

An e-ink "oracle" for the room: a Raspberry Pi 4 that shows weather, indoor air conditions, gaming news/deals, and album art for whatever song is playing nearby.

Owner is a beginner-to-intermediate maker learning as they go. Explain non-obvious hardware/Linux steps briefly, prefer simple readable Python over clever code, and keep changes small enough to test one at a time.

## Status

- **Phase 1 (current):** Pi + sensors + mic, no screen yet. The dashboard renders to PNG files and is previewed in a browser.
- **Phase 2 (later):** add a 13.3" Spectra 6 e-ink display (Pimoroni Inky Impression 13.3" PIM774, or Waveshare 13.3" e-Paper HAT+ (E) as fallback) and an A4 shadow-box frame.

Display output must stay behind a single interface so switching between PNG preview, Inky, and Waveshare is a config change, not a rewrite.

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

## Display constraints (design for these)

- Canvas: **1600 × 1200** landscape (4:3).
- Palette is exactly six colours: black, white, red, yellow, green, blue. Everything else is dithered.
  - Text, icons, and UI elements: use solid palette colours only. No anti-aliased grey text, no gradients, no thin pastel lines.
  - Photos/album art: dither to the 6-colour palette and accept a screen-printed look.
- Refresh is slow and flashes. Only push a new frame when content actually changes. Never animate. Target updates every 5–15 minutes, or on song change.
- The preview pipeline must produce **two** images: `latest.png` (full colour, for layout work) and `latest_eink.png` (quantised + dithered to the 6-colour palette) so designs are judged as they will really look.

## Software stack

- **Python 3** in a virtual environment (Raspberry Pi OS Bookworm blocks system-wide pip installs). Create with `python3 -m venv .venv` and activate before installing.
- **Pillow** for drawing the frame; **requests** for HTTP.
- Sensors: Pimoroni's BME68x library (or `bme680` package, which supports BME688 basics); Adafruit `adafruit-circuitpython-scd4x` for the SCD-41 (needs `adafruit-blinka`). Confirm current package names before installing.
- Display (Phase 2): Pimoroni `inky` library, or Waveshare's Python driver.
- Audio: `sounddevice` (or `arecord`) to capture; `shazamio` for recognition (unofficial, can break), with ACRCloud or AudD as fallback. Spotify's "currently playing" API is an optional primary source.
- Weather: Open-Meteo (free, no API key).
- Runs as a **systemd service** so it starts on boot and restarts on crash.

## Architecture

```
delphi/
  CLAUDE.md
  config.toml            # location, API keys, update intervals, display backend
  main.py                # main loop: gather → render → output (only if changed)
  sources/               # one module per data source, each returns plain data
    weather.py
    sensors.py           # BME688 + SCD-41; returns None per field on failure
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

## Modes

1. **Dashboard** (default): weather, indoor conditions with CO2 "open a window" warning (>1000 ppm), gaming panel, clock/date.
2. **Now playing:** when music is detected and identified, show album art + title/artist. Return to dashboard after a few minutes of silence.
3. Phase 2: the Inky's four buttons switch modes, force refresh, and mute the mic.

## Privacy

The mic is always listening for music. Keep clips short (~5–10 s), process in memory, **never write audio to disk or log it**, and provide a mute toggle.

## Dev workflow

- Edit on the PC in VS Code using **Remote – SSH** to `delphi.local`, or develop layout locally with `--fake`.
- Run manually: `source .venv/bin/activate && python main.py` (add `--fake` off-Pi).
- Preview: open `http://delphi.local:8000` (or the local preview server) to see the latest e-ink simulation.
- Useful checks: `i2cdetect -y 1` (sensors), `arecord -l` (mic), `vcgencmd get_throttled` (power problems; anything other than `0x0` means a power or heat issue).

## Roadmap

1. Read both sensors and print values.
2. Fake-data dashboard rendering to PNG + e-ink simulation, with browser preview.
3. Live weather + live sensors on the dashboard.
4. Song recognition and now-playing mode.
5. Gaming panel (free games of the week, news).
6. systemd service, logging, read-only-friendly setup to protect the SD card.
7. Phase 2: display backend, buttons, frame.

Later ideas: calendar, transit departures, Twitch live list, Steam wishlist deals, museum art of the day, wireless room sensors (ESP32 or BLE thermometers).
