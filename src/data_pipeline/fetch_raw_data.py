"""
Pulls raw historical data (rainfall, temperature, humidity, soil moisture,
river discharge) for Nairobi, Kisumu, and Tana River (Hola) from Open-Meteo's
free, no-API-key endpoints. Saves each response untouched to data/raw/.

Run from the project root: python src/data_pipeline/fetch_raw_data.py
"""

import json
import time
from pathlib import Path

import requests

REGIONS = {
    "nairobi": {"lat": -1.2864, "lon": 36.8172},
    "kisumu": {"lat": -0.1022, "lon": 34.7617},
    "tana_river_hola": {"lat": -1.4941, "lon": 40.0280},
}

START_DATE = "2015-01-01"
END_DATE = "2024-12-31"

WEATHER_URL = "https://archive-api.open-meteo.com/v1/archive"
WEATHER_DAILY_VARS = [
    "precipitation_sum",
    "temperature_2m_mean",
    "relative_humidity_2m_mean",
    "soil_moisture_0_to_7cm_mean",
]

FLOOD_URL = "https://flood-api.open-meteo.com/v1/flood"
FLOOD_DAILY_VARS = ["river_discharge"]

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)


def fetch(url: str, params: dict, retries: int = 3, backoff: float = 2.0) -> dict:
    """GET a JSON endpoint with retries; raise only if every attempt fails."""
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            last_error = exc
            print(f"  attempt {attempt}/{retries} failed: {exc}")
            if attempt < retries:
                time.sleep(backoff * attempt)
    raise RuntimeError(f"Failed to fetch {url} with params {params}") from last_error


def check_for_all_null_columns(data: dict, region: str, source: str) -> None:
    """Catch a wrong/unsupported variable name immediately, not two steps later."""
    daily = data.get("daily", {})
    for key, values in daily.items():
        if key == "time":
            continue
        non_null = sum(1 for v in values if v is not None)
        if non_null == 0:
            print(f"  *** WARNING: [{region}/{source}] '{key}' came back 100% null. "
                  f"This usually means the variable name is wrong for this endpoint "
                  f"-- double-check it against the Open-Meteo docs before trusting this file.")


def fetch_weather(lat: float, lon: float) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": START_DATE,
        "end_date": END_DATE,
        "daily": ",".join(WEATHER_DAILY_VARS),
        "timezone": "Africa/Nairobi",
    }
    return fetch(WEATHER_URL, params)


def fetch_flood(lat: float, lon: float) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": START_DATE,
        "end_date": END_DATE,
        "daily": ",".join(FLOOD_DAILY_VARS),
        "timezone": "Africa/Nairobi",
    }
    return fetch(FLOOD_URL, params)


def save_json(data: dict, filename: str) -> None:
    path = RAW_DIR / filename
    with open(path, "w") as f:
        json.dump(data, f)
    print(f"  saved {path} ({path.stat().st_size:,} bytes)")


def main():
    for region, coords in REGIONS.items():
        lat, lon = coords["lat"], coords["lon"]

        print(f"[{region}] fetching weather data...")
        weather_data = fetch_weather(lat, lon)
        check_for_all_null_columns(weather_data, region, "weather")
        save_json(weather_data, f"{region}_weather.json")
        time.sleep(1)

        print(f"[{region}] fetching river discharge data...")
        flood_data = fetch_flood(lat, lon)
        check_for_all_null_columns(flood_data, region, "flood")
        save_json(flood_data, f"{region}_flood.json")
        time.sleep(1)

    print("\nDone. 6 files should now be in data/raw/")


if __name__ == "__main__":
    main()
