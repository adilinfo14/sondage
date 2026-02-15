import threading
import tkinter as tk
from tkinter import ttk, messagebox
import requests


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


class WeatherApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Météo du monde")
        self.root.geometry("760x560")

        self.city_var = tk.StringVar()
        self.selected_place: dict | None = None
        self.suggestion_places: list[dict] = []
        self.suggestion_job: str | None = None
        self.suggestion_request_id = 0
        self.suppress_autocomplete = False

        self._build_ui()

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=16)
        container.pack(fill="both", expand=True)

        title = ttk.Label(
            container,
            text="🌍 Application météo du monde",
            font=("Segoe UI", 18, "bold"),
        )
        title.pack(anchor="w", pady=(0, 12))

        search_frame = ttk.Frame(container)
        search_frame.pack(fill="x", pady=(0, 10))

        self.city_entry = ttk.Entry(search_frame, textvariable=self.city_var)
        self.city_entry.pack(side="left", fill="x", expand=True)
        self.city_entry.bind("<Return>", lambda _: self.fetch_weather())
        self.city_entry.bind("<Down>", self._focus_suggestions)
        self.city_entry.bind("<Escape>", lambda _: self._hide_suggestions())

        self.search_btn = ttk.Button(search_frame, text="Rechercher", command=self.fetch_weather)
        self.search_btn.pack(side="left", padx=(8, 0))

        self.suggestions_frame = ttk.Frame(container)
        self.suggestions_frame.pack(fill="x", pady=(0, 8))

        self.suggestions_listbox = tk.Listbox(self.suggestions_frame, height=5)
        self.suggestions_listbox.pack(fill="x")
        self.suggestions_listbox.pack_forget()
        self.suggestions_listbox.bind("<<ListboxSelect>>", self._on_suggestion_select)
        self.suggestions_listbox.bind("<Return>", self._on_suggestion_activate)
        self.suggestions_listbox.bind("<Double-Button-1>", self._on_suggestion_activate)
        self.suggestions_listbox.bind("<Escape>", lambda _: self._hide_suggestions())

        self.city_var.trace_add("write", self._on_city_input)

        self.status_var = tk.StringVar(value="Entre une ville (ex: Tokyo, Paris, Nairobi)")
        status_lbl = ttk.Label(container, textvariable=self.status_var, foreground="#555")
        status_lbl.pack(anchor="w", pady=(0, 12))

        self.location_var = tk.StringVar(value="")
        location_lbl = ttk.Label(container, textvariable=self.location_var, font=("Segoe UI", 13, "bold"))
        location_lbl.pack(anchor="w", pady=(0, 8))

        self.current_var = tk.StringVar(value="")
        current_lbl = ttk.Label(container, textvariable=self.current_var, font=("Segoe UI", 11))
        current_lbl.pack(anchor="w", pady=(0, 12))

        forecast_title = ttk.Label(container, text="Prévisions 5 jours", font=("Segoe UI", 12, "bold"))
        forecast_title.pack(anchor="w")

        self.forecast_text = tk.Text(container, height=18, wrap="word", state="disabled")
        self.forecast_text.pack(fill="both", expand=True, pady=(8, 0))

    def fetch_weather(self) -> None:
        city = self.city_var.get().strip()
        if not city:
            messagebox.showwarning("Ville manquante", "Veuillez entrer un nom de ville.")
            return

        selected_place: dict | None = None
        if self.selected_place and city == self._format_place_label(self.selected_place):
            selected_place = self.selected_place

        self._hide_suggestions()
        self.search_btn.config(state="disabled")
        self.status_var.set("Recherche en cours...")

        thread = threading.Thread(target=self._fetch_weather_thread, args=(city, selected_place), daemon=True)
        thread.start()

    def _fetch_weather_thread(self, city: str, selected_place: dict | None = None) -> None:
        try:
            if selected_place is not None:
                place = selected_place
            else:
                geo_data = self._get_geocoding(city)
                if not geo_data:
                    self.root.after(
                        0,
                        lambda: self._handle_error("Aucune ville trouvée. Vérifie l’orthographe et réessaie."),
                    )
                    return
                place = geo_data[0]

            forecast = self._get_forecast(place["latitude"], place["longitude"])

            self.root.after(0, lambda: self._update_ui(place, forecast))
        except Exception as exc:
            self.root.after(0, lambda: self._handle_error(f"Erreur réseau: {exc}"))

    def _get_geocoding(self, city: str) -> list[dict]:
        params = {
            "name": city,
            "count": 5,
            "language": "fr",
            "format": "json",
        }
        response = requests.get(GEOCODING_URL, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        return data.get("results", [])

    def _on_city_input(self, *_: object) -> None:
        if self.suppress_autocomplete:
            return

        self.selected_place = None
        query = self.city_var.get().strip()

        if self.suggestion_job:
            self.root.after_cancel(self.suggestion_job)
            self.suggestion_job = None

        if len(query) < 2:
            self._hide_suggestions()
            return

        self.suggestion_request_id += 1
        request_id = self.suggestion_request_id
        self.suggestion_job = self.root.after(300, lambda: self._load_suggestions(query, request_id))

    def _load_suggestions(self, query: str, request_id: int) -> None:
        thread = threading.Thread(target=self._load_suggestions_thread, args=(query, request_id), daemon=True)
        thread.start()

    def _load_suggestions_thread(self, query: str, request_id: int) -> None:
        try:
            places = self._get_geocoding(query)
            self.root.after(0, lambda: self._show_suggestions(query, request_id, places))
        except Exception:
            self.root.after(0, self._hide_suggestions)

    def _show_suggestions(self, query: str, request_id: int, places: list[dict]) -> None:
        current_value = self.city_var.get().strip()
        if request_id != self.suggestion_request_id or query != current_value:
            return

        if not places:
            self._hide_suggestions()
            return

        self.suggestion_places = places
        self.suggestions_listbox.delete(0, tk.END)
        for place in places:
            self.suggestions_listbox.insert(tk.END, self._format_place_label(place))

        if not self.suggestions_listbox.winfo_ismapped():
            self.suggestions_listbox.pack(fill="x")

    def _focus_suggestions(self, event: tk.Event) -> str | None:
        if not self.suggestion_places:
            return None

        self.suggestions_listbox.focus_set()
        self.suggestions_listbox.selection_clear(0, tk.END)
        self.suggestions_listbox.selection_set(0)
        self.suggestions_listbox.activate(0)
        return "break"

    def _on_suggestion_select(self, _: tk.Event) -> None:
        selection = self.suggestions_listbox.curselection()
        if not selection:
            return

        index = selection[0]
        if index >= len(self.suggestion_places):
            return

        place = self.suggestion_places[index]
        self.selected_place = place
        self.suppress_autocomplete = True
        self.city_var.set(self._format_place_label(place))
        self.suppress_autocomplete = False
        self.city_entry.icursor(tk.END)
        self.city_entry.focus_set()
        self._hide_suggestions()

    def _on_suggestion_activate(self, event: tk.Event) -> str:
        self._on_suggestion_select(event)
        self.fetch_weather()
        return "break"

    def _hide_suggestions(self) -> None:
        self.suggestion_places = []
        self.suggestions_listbox.delete(0, tk.END)
        self.suggestions_listbox.pack_forget()

    def _format_place_label(self, place: dict) -> str:
        name = place.get("name", "Ville")
        admin = place.get("admin1")
        country = place.get("country", "Pays")
        if admin:
            return f"{name}, {admin}, {country}"
        return f"{name}, {country}"

    def _get_forecast(self, latitude: float, longitude: float) -> dict:
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

    def _update_ui(self, place: dict, forecast: dict) -> None:
        current = forecast.get("current", {})
        daily = forecast.get("daily", {})

        city_name = place.get("name", "Ville inconnue")
        country = place.get("country", "Pays inconnu")
        admin = place.get("admin1")
        if admin:
            location = f"{city_name}, {admin}, {country}"
        else:
            location = f"{city_name}, {country}"

        weather_label = WEATHER_CODES.get(current.get("weather_code"), "Description indisponible")
        current_text = (
            f"Actuellement: {current.get('temperature_2m', 'N/A')}°C • {weather_label} • "
            f"Humidité: {current.get('relative_humidity_2m', 'N/A')}% • "
            f"Vent: {current.get('wind_speed_10m', 'N/A')} km/h"
        )

        lines = []
        dates = daily.get("time", [])
        max_temps = daily.get("temperature_2m_max", [])
        min_temps = daily.get("temperature_2m_min", [])
        precip = daily.get("precipitation_probability_max", [])
        codes = daily.get("weather_code", [])

        for index, date_str in enumerate(dates):
            raw_code = codes[index] if index < len(codes) else None
            code = raw_code if isinstance(raw_code, int) else -1
            weather_day = WEATHER_CODES.get(code, "Description indisponible")
            max_temp = max_temps[index] if index < len(max_temps) else "N/A"
            min_temp = min_temps[index] if index < len(min_temps) else "N/A"
            rain = precip[index] if index < len(precip) else "N/A"
            lines.append(
                f"• {date_str}  |  {weather_day}\n"
                f"  Temp: {min_temp}°C → {max_temp}°C  |  Pluie: {rain}%"
            )

        self.location_var.set(location)
        self.current_var.set(current_text)

        self.forecast_text.config(state="normal")
        self.forecast_text.delete("1.0", tk.END)
        self.forecast_text.insert(tk.END, "\n\n".join(lines))
        self.forecast_text.config(state="disabled")

        self.status_var.set("Météo mise à jour.")
        self.search_btn.config(state="normal")

    def _handle_error(self, message: str) -> None:
        self.search_btn.config(state="normal")
        self.status_var.set("Échec de la récupération météo.")
        messagebox.showerror("Erreur", message)


def main() -> None:
    root = tk.Tk()
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    WeatherApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
