const cityInput = document.getElementById("cityInput");
const weatherForm = document.getElementById("weatherForm");
const suggestionsEl = document.getElementById("suggestions");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");

const locationEl = document.getElementById("location");
const tempEl = document.getElementById("temp");
const weatherEl = document.getElementById("weather");
const humidityEl = document.getElementById("humidity");
const windEl = document.getElementById("wind");
const forecastEl = document.getElementById("forecast");
const currentIconEl = document.getElementById("currentIcon");
const themeToggleBtn = document.getElementById("themeToggle");

let debounceTimer = null;
let selectedCity = null;
let suggestions = [];
let activeIndex = -1;

const THEME_KEY = "meteo-theme";

function getWeatherVisual(weatherText = "") {
    const label = weatherText.toLowerCase();

    if (label.includes("orage") || label.includes("grêle")) {
        return { emoji: "⛈️", className: "icon-storm" };
    }
    if (label.includes("neige")) {
        return { emoji: "❄️", className: "icon-snow" };
    }
    if (label.includes("pluie") || label.includes("averse") || label.includes("bruine")) {
        return { emoji: "🌧️", className: "icon-rain" };
    }
    if (label.includes("brouillard")) {
        return { emoji: "🌫️", className: "icon-fog" };
    }
    if (label.includes("nuage") || label.includes("couvert")) {
        return { emoji: "☁️", className: "icon-cloud" };
    }
    return { emoji: "☀️", className: "icon-sun" };
}

function applyTheme(theme) {
    const normalizedTheme = theme === "light" ? "light" : "dark";
    document.body.setAttribute("data-theme", normalizedTheme);
    localStorage.setItem(THEME_KEY, normalizedTheme);
    if (themeToggleBtn) {
        themeToggleBtn.textContent = normalizedTheme === "light" ? "🌞 Jour" : "🌙 Nuit";
    }
}

function setStatus(message) {
    statusEl.textContent = message;
}

function hideSuggestions() {
    suggestions = [];
    activeIndex = -1;
    suggestionsEl.innerHTML = "";
    suggestionsEl.classList.add("hidden");
}

function renderSuggestions(items) {
    suggestions = items;
    activeIndex = -1;

    if (!items.length) {
        hideSuggestions();
        return;
    }

    suggestionsEl.innerHTML = "";
    items.forEach((item, index) => {
        const li = document.createElement("li");
        li.textContent = item.label;
        li.dataset.index = String(index);
        li.addEventListener("mousedown", (event) => {
            event.preventDefault();
            chooseSuggestion(index);
        });
        suggestionsEl.appendChild(li);
    });

    suggestionsEl.classList.remove("hidden");
}

function updateActiveSuggestion() {
    const nodes = suggestionsEl.querySelectorAll("li");
    nodes.forEach((node, idx) => {
        node.classList.toggle("active", idx === activeIndex);
    });
}

function chooseSuggestion(index) {
    const choice = suggestions[index];
    if (!choice) {
        return;
    }

    selectedCity = choice;
    cityInput.value = choice.label;
    hideSuggestions();
}

async function fetchSuggestions(query) {
    if (query.length < 2) {
        hideSuggestions();
        setStatus("Entre au moins 2 caractères pour lancer l’autocomplétion.");
        return;
    }

    try {
        const response = await fetch(`/api/suggest?q=${encodeURIComponent(query)}`);
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || "Erreur suggestions");
        }

        if (cityInput.value.trim() !== query) {
            return;
        }

        renderSuggestions(data);
        setStatus(data.length ? "Choisis une ville dans la liste ou valide ta saisie." : "Aucune suggestion trouvée.");
    } catch (error) {
        hideSuggestions();
        setStatus(error.message || "Impossible de charger les suggestions.");
    }
}

function renderWeather(payload) {
    resultEl.classList.remove("hidden");
    locationEl.textContent = payload.location;
    tempEl.textContent = `${payload.current.temperature ?? "--"}°C`;
    weatherEl.textContent = payload.current.weather || "--";
    humidityEl.textContent = `Humidité: ${payload.current.humidity ?? "--"}%`;
    windEl.textContent = `Vent: ${payload.current.wind ?? "--"} km/h`;

    const currentVisual = getWeatherVisual(payload.current.weather || "");
    currentIconEl.textContent = currentVisual.emoji;
    currentIconEl.className = `weather-icon ${currentVisual.className}`;

    forecastEl.innerHTML = "";
    payload.daily.forEach((day) => {
        const visual = getWeatherVisual(day.weather || "");
        const card = document.createElement("div");
        card.className = "day";
        card.innerHTML = `
            <p class="day-icon ${visual.className}" aria-hidden="true">${visual.emoji}</p>
            <p><strong>${day.date}</strong></p>
            <p>${day.weather}</p>
            <p>${day.temp_min ?? "--"}°C → ${day.temp_max ?? "--"}°C</p>
            <p>Pluie: ${day.rain ?? "--"}%</p>
        `;
        forecastEl.appendChild(card);
    });
}

async function fetchWeather() {
    const city = cityInput.value.trim();
    if (!city) {
        setStatus("Veuillez entrer une ville.");
        return;
    }

    setStatus("Récupération météo en cours...");
    hideSuggestions();

    const params = new URLSearchParams();
    params.set("city", city);

    if (selectedCity && selectedCity.label === city) {
        params.set("lat", selectedCity.latitude);
        params.set("lon", selectedCity.longitude);
        if (selectedCity.country) {
            params.set("country", selectedCity.country);
        }
        if (selectedCity.admin1) {
            params.set("admin1", selectedCity.admin1);
        }
    }

    try {
        const response = await fetch(`/api/weather?${params.toString()}`);
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || "Erreur météo");
        }

        renderWeather(data);
        setStatus("Météo mise à jour ✅");
    } catch (error) {
        setStatus(error.message || "Impossible de récupérer la météo.");
    }
}

cityInput.addEventListener("input", () => {
    selectedCity = null;

    if (debounceTimer) {
        clearTimeout(debounceTimer);
    }

    const query = cityInput.value.trim();
    debounceTimer = setTimeout(() => {
        fetchSuggestions(query);
    }, 250);
});

cityInput.addEventListener("keydown", (event) => {
    if (suggestionsEl.classList.contains("hidden")) {
        return;
    }

    if (event.key === "ArrowDown") {
        event.preventDefault();
        activeIndex = Math.min(activeIndex + 1, suggestions.length - 1);
        updateActiveSuggestion();
    } else if (event.key === "ArrowUp") {
        event.preventDefault();
        activeIndex = Math.max(activeIndex - 1, 0);
        updateActiveSuggestion();
    } else if (event.key === "Enter" && activeIndex >= 0) {
        event.preventDefault();
        chooseSuggestion(activeIndex);
        fetchWeather();
    } else if (event.key === "Escape") {
        hideSuggestions();
    }
});

weatherForm.addEventListener("submit", (event) => {
    event.preventDefault();
    fetchWeather();
});

document.addEventListener("click", (event) => {
    if (!weatherForm.contains(event.target)) {
        hideSuggestions();
    }
});

const savedTheme = localStorage.getItem(THEME_KEY);
applyTheme(savedTheme || "dark");

if (themeToggleBtn) {
    themeToggleBtn.addEventListener("click", () => {
        const currentTheme = document.body.getAttribute("data-theme") || "dark";
        applyTheme(currentTheme === "dark" ? "light" : "dark");
    });
}
