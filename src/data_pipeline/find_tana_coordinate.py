"""
One-off diagnostic: GloFAS river discharge is a 5km-grid model, and a
coordinate can easily miss the actual modelled channel. This tests a small
grid of points around the original Hola coordinate and reports which ones
return a real (non-zero, non-constant) discharge series, using just one
year of data so it's fast.

Run from the project root: python src/data_pipeline/find_tana_coordinate.py
"""

import time
import requests

BASE_LAT, BASE_LON = -1.4941, 40.0280
STEP = 0.05  # roughly one GloFAS grid cell (~5km)

CANDIDATES = [
    (BASE_LAT + dlat * STEP, BASE_LON + dlon * STEP)
    for dlat in [-1, 0, 1]
    for dlon in [-1, 0, 1]
]

FLOOD_URL = "https://flood-api.open-meteo.com/v1/flood"


def check(lat: float, lon: float) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": "2024-01-01",
        "end_date": "2024-12-31",
        "daily": "river_discharge",
        "timezone": "Africa/Nairobi",
    }
    response = requests.get(FLOOD_URL, params=params, timeout=30)
    response.raise_for_status()
    values = [v for v in response.json()["daily"]["river_discharge"] if v is not None]
    if not values:
        return {"mean": None, "max": None, "nonzero_days": 0}
    nonzero = [v for v in values if v > 0]
    return {
        "mean": sum(values) / len(values),
        "max": max(values),
        "nonzero_days": len(nonzero),
        "total_days": len(values),
    }


def main():
    print(f"{'lat':>8} {'lon':>8} {'mean':>10} {'max':>10} {'nonzero days':>14}")
    for lat, lon in CANDIDATES:
        stats = check(lat, lon)
        if stats["mean"] is None:
            print(f"{lat:8.4f} {lon:8.4f} {'no data':>10}")
        else:
            print(f"{lat:8.4f} {lon:8.4f} {stats['mean']:10.2f} {stats['max']:10.2f} "
                  f"{stats['nonzero_days']:6d}/{stats['total_days']}")
        time.sleep(1)
    print(f"\nOriginal Hola coordinate was: {BASE_LAT}, {BASE_LON}")
    print("Look for the row with the highest mean and most nonzero days -- that's")
    print("almost certainly the one actually sitting on the Tana River's channel.")


if __name__ == "__main__":
    main()
