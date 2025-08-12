# WeatherWise (Tech Assessment 1 & 2)

A complete weather app implementing both assessments (including optional extras).

**Features**
- Input location by **city/landmark**, **US ZIP**, or **lat,lon**.
- Fetch **current weather** and **5-day forecast (or any range up to 31 days)** from Open-Meteo.
- **Use my location** (browser geolocation) for one-click current area weather.
- **CRUD** with SQLite persistence (FastAPI backend).
- **Read** previous requests; **Update** request (re-fetch); **Delete**.
- **Data export** in JSON, CSV, Markdown, and **PDF**.
- **Optional APIs**: Wikipedia summary, YouTube search (API or link-fallback), map link (Google Static Maps if key, else OpenStreetMap).
- Input & range **validation** with clear error messages.
- Simple icons, clean layout, and an **Info** button referencing **PM Accelerator**.

## Stack
- **Backend**: FastAPI, SQLAlchemy, SQLite, ReportLab
- **Frontend**: Static HTML + JS (no build step), fetch-based UI

## Run Locally

1) Start backend:
```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env  # optionally fill API keys
uvicorn app:app --reload --port 8000
```

2) Open the frontend:
- Simply open `frontend/index.html` in your browser **OR** serve it:
```bash
# quick Python web server (optional)
cd frontend
python -m http.server 5500
# then visit http://localhost:5500
```

> The frontend points at `http://localhost:8000` by default. Adjust CORS in `backend/.env` if needed.

## API Keys (Optional)
- `YOUTUBE_API_KEY`: show top 5 video results; otherwise a YouTube **search link** is returned.
- `GOOGLE_STATIC_MAPS_KEY`: return a Static Map image URL; otherwise we return an **OpenStreetMap** link.

## Demo Script (for recording)
- Show creating a request for **"San Francisco"** with today → +4 days.
- Click **Use my location** and fetch again.
- View **Saved Requests**, open **View** to see the daily tiles.
- Click **Update** to change to **"94105"** or **"37.77,-122.42"**.
- Export **CSV** and **PDF**.
- Try **Wikipedia** and **YouTube** for the city.
- Open the map link.

---

Built on 2025-08-10 10:38:31 UTC.
