# WeatherWise API (Backend)

FastAPI + SQLite backend for the WeatherWise app. Implements:
- **Create** weather requests with location + date range (validates, geocodes, fetches real data from Open-Meteo, stores in DB).
- **Read** list/details of previous requests.
- **Update** any request (re-geocodes + re-fetches).
- **Delete** a request.
- **Export** data in JSON, CSV, Markdown, and PDF.
- **Optional APIs**: Wikipedia summaries, YouTube search (API or link fallback), map link or Static Map.

Created: 2025-08-10 10:38:31 UTC

## Quick Start

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Copy env and edit if desired
cp .env.example .env

# Start the server
uvicorn app:app --reload --port 8000
```

API base: `http://localhost:8000`

## Key Endpoints

- `POST /api/requests` — body:
```json
{
  "location": "San Diego, CA",
  "date_from": "2025-08-10",
  "date_to": "2025-08-14"
}
```
Supports location formats:
- Name/landmark: "Golden Gate Bridge", "Paris", "Tokyo Station"
- **US ZIP**: "94105" (via Zippopotam.us)
- **Lat,lon**: "37.7749,-122.4194"

- `GET /api/requests` — list recent (limit=50)
- `GET /api/requests/{id}` — details
- `PUT /api/requests/{id}` — update (location/date range/notes)
- `DELETE /api/requests/{id}` — delete

- `GET /api/export?format=json|csv|md|pdf`

- `GET /api/info?q=San%20Francisco` — Wikipedia summary
- `GET /api/media/youtube?q=San%20Francisco` — YouTube search (uses API key if provided, else returns a search URL)
- `GET /api/map?lat=37.77&lon=-122.42` — Google Static Maps (if key), else OSM link

## Notes

- Weather is fetched from **Open-Meteo** (no key required). Archive API is used for past dates.
- Geocoding is handled by **Open-Meteo Geocoding** and **Zippopotam.us** (for US ZIPs). Reverse geocoding improves display names.
- Date ranges are limited to **31 days** for performance and API limits.
- **CORS** defaults to localhost:8000. Adjust `CORS_ORIGINS` in `.env` if needed.

## Optional Keys

- `YOUTUBE_API_KEY` — enable YouTube Data API v3 results instead of a simple search URL.
- `GOOGLE_STATIC_MAPS_KEY` — enable Google Static Maps image links. Without a key, OSM link is returned.
