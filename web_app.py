from __future__ import annotations

from typing import Any

import requests
from flask import Flask, jsonify, render_template, request


GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

WEATHER_CODES = {
    0: "Ciel dégagé",
    1: "Principalement dégagé",
    2: "Partiellement nuageux",
    3: "Couvert",
    45: "Brouillard",
    48: "Brouillard givrant",
    51: "Bruine légère",
    53: "Bruine modérée",
    55: "Bruine dense",
    56: "Bruine verglaçante légère",
    57: "Bruine verglaçante dense",
    61: "Pluie faible",
    63: "Pluie modérée",
    65: "Pluie forte",
    66: "Pluie verglaçante légère",
    67: "Pluie verglaçante forte",
    71: "Neige faible",
    73: "Neige modérée",
    75: "Neige forte",
    77: "Grains de neige",
    80: "Averses faibles",
    81: "Averses modérées",
    82: "Averses violentes",
    85: "Averses de neige faibles",
    86: "Averses de neige fortes",
    95: "Orage",
    96: "Orage avec grêle faible",
    99: "Orage avec grêle forte",
}

app = Flask(__name__)


def get_geocoding(city: str, count: int = 8) -> list[dict[str, Any]]:
    params = {
        "name": city,
        "count": count,
        "language": "fr",
        "format": "json",
    }
    response = requests.get(GEOCODING_URL, params=params, timeout=15)
    response.raise_for_status()
    data = response.json()
    return data.get("results", [])


def get_forecast(latitude: float, longitude: float) -> dict[str, Any]:
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone": "auto",
        "forecast_days": 5,
    }
    response = requests.get(FORECAST_URL, params=params, timeout=15)
    response.raise_for_status()
    return response.json()


def format_place_label(place: dict[str, Any]) -> str:
    name = place.get("name", "Ville")
    admin = place.get("admin1")
    country = place.get("country", "Pays")
    if admin:
        return f"{name}, {admin}, {country}"
    return f"{name}, {country}"


@app.get("/")
def home() -> str:
    return render_template("index.html")


@app.get("/api/suggest")
def suggest() -> tuple[Any, int] | Any:
    query = request.args.get("q", "").strip()
    if len(query) < 2:
        return jsonify([])

    try:
        places = get_geocoding(query, count=8)
    except requests.RequestException:
        return jsonify({"error": "Impossible de récupérer les suggestions de villes."}), 502

    results = []
    for place in places:
        results.append(
            {
                "label": format_place_label(place),
                "name": place.get("name"),
                "country": place.get("country"),
                "admin1": place.get("admin1"),
                "latitude": place.get("latitude"),
                "longitude": place.get("longitude"),
            }
        )

    return jsonify(results)


@app.get("/api/weather")
def weather() -> tuple[Any, int] | Any:
    city = request.args.get("city", "").strip()
    latitude = request.args.get("lat", type=float)
    longitude = request.args.get("lon", type=float)

    if not city and (latitude is None or longitude is None):
        return jsonify({"error": "Ville ou coordonnées manquantes."}), 400

    try:
        if latitude is not None and longitude is not None:
            place = {
                "name": city or "Ville sélectionnée",
                "country": request.args.get("country", "Pays inconnu"),
                "admin1": request.args.get("admin1"),
                "latitude": latitude,
                "longitude": longitude,
            }
        else:
            places = get_geocoding(city, count=1)
            if not places:
                return jsonify({"error": "Aucune ville trouvée."}), 404
            place = places[0]

        forecast = get_forecast(place["latitude"], place["longitude"])
    except requests.RequestException:
        return jsonify({"error": "Impossible de récupérer la météo pour le moment."}), 502

    current = forecast.get("current", {})
    daily = forecast.get("daily", {})

    raw_current_code = current.get("weather_code")
    current_code = raw_current_code if isinstance(raw_current_code, int) else -1

    days = []
    dates = daily.get("time", [])
    max_temps = daily.get("temperature_2m_max", [])
    min_temps = daily.get("temperature_2m_min", [])
    precip_probs = daily.get("precipitation_probability_max", [])
    daily_codes = daily.get("weather_code", [])

    for index, date_str in enumerate(dates):
        raw_code = daily_codes[index] if index < len(daily_codes) else None
        code = raw_code if isinstance(raw_code, int) else -1
        days.append(
            {
                "date": date_str,
                "weather": WEATHER_CODES.get(code, "Description indisponible"),
                "temp_min": min_temps[index] if index < len(min_temps) else None,
                "temp_max": max_temps[index] if index < len(max_temps) else None,
                "rain": precip_probs[index] if index < len(precip_probs) else None,
            }
        )

    return jsonify(
        {
            "location": format_place_label(place),
            "current": {
                "temperature": current.get("temperature_2m"),
                "humidity": current.get("relative_humidity_2m"),
                "wind": current.get("wind_speed_10m"),
                "weather": WEATHER_CODES.get(current_code, "Description indisponible"),
            },
            "daily": days,
        }
    )


if __name__ == "__main__":
    app.run(debug=True)
