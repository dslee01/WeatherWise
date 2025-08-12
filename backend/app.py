\
import os
import io
import csv
import json
import math
import time
import base64
import string
import typing as t
from datetime import date, datetime, timedelta

import requests
from fastapi import FastAPI, HTTPException, Depends, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, Text
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# ---------------- Settings ----------------
class Settings(BaseSettings):
    YOUTUBE_API_KEY: str | None = None
    GOOGLE_STATIC_MAPS_KEY: str | None = None
    CORS_ORIGINS: str = "http://localhost:8000,http://127.0.0.1:8000"
    DATABASE_URL: str = "sqlite:///./weatherwise.db"

settings = Settings()

# ---------------- DB ----------------
engine = create_engine(settings.DATABASE_URL, connect_args={"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class WeatherRequest(Base):
    __tablename__ = "weather_requests"
    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    location_input = Column(String, nullable=False)
    resolved_name = Column(String, nullable=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    date_from = Column(Date, nullable=False)
    date_to = Column(Date, nullable=False)
    provider = Column(String, default="open-meteo", nullable=False)
    weather_json = Column(Text, nullable=False)  # store daily temps & current weather
    notes = Column(Text, nullable=True)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ---------------- Pydantic Schemas ----------------
class CreateRequest(BaseModel):
    location: str = Field(..., description="User-entered location: name, landmark, 'lat,lon', or postal code.")
    date_from: date
    date_to: date

    @field_validator("date_to")
    @classmethod
    def ensure_order(cls, v, info):
        data = info.data
        if "date_from" in data and data["date_from"] and v < data["date_from"]:
            raise ValueError("date_to cannot be earlier than date_from")
        return v

class UpdateRequest(BaseModel):
    location: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    notes: str | None = None

class RequestOut(BaseModel):
    id: int
    created_at: datetime
    location_input: str
    resolved_name: str | None
    latitude: float
    longitude: float
    date_from: date
    date_to: date
    provider: str
    weather: dict
    notes: str | None

# ---------------- Utilities ----------------
def parse_latlon(s: str) -> tuple[float, float] | None:
    try:
        if "," in s:
            a, b = s.split(",", 1)
            return float(a.strip()), float(b.strip())
    except Exception:
        return None
    return None

def is_us_zip(s: str) -> bool:
    return s.isdigit() and len(s) == 5

def clamp_days(dfrom: date, dto: date, max_days: int = 31):
    delta = (dto - dfrom).days + 1
    if delta > max_days:
        raise HTTPException(status_code=400, detail=f"Date range too large ({delta} days). Max {max_days} days.")

def geocode(location: str) -> tuple[str, float, float]:
    # Try lat,lon
    latlon = parse_latlon(location)
    if latlon:
        # Reverse geocode to a friendly name (optional)
        name = reverse_geocode(latlon[0], latlon[1])
        return name or f"{latlon[0]:.4f},{latlon[1]:.4f}", latlon[0], latlon[1]

    # Try US ZIP via Zippopotam.us (no key, free)
    if is_us_zip(location):
        try:
            r = requests.get(f"https://api.zippopotam.us/us/{location}", timeout=10)
            if r.status_code == 200:
                data = r.json()
                place = data["places"][0]
                lat = float(place["latitude"])
                lon = float(place["longitude"])
                name = f"{place['place name']}, {place['state abbreviation']} {location}"
                return name, lat, lon
        except Exception:
            pass  # fallthrough

    # Fallback to Open-Meteo Geocoding
    r = requests.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": location, "count": 1, "language": "en"}, timeout=15)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="Geocoding failed")
    j = r.json()
    results = j.get("results") or []
    if not results:
        raise HTTPException(status_code=404, detail="Location not found")
    top = results[0]
    name_parts = [top.get("name")]
    if top.get("admin1"):
        name_parts.append(top["admin1"])
    if top.get("country"):
        name_parts.append(top["country"])
    name = ", ".join([p for p in name_parts if p])
    return name, float(top["latitude"]), float(top["longitude"])

def reverse_geocode(lat: float, lon: float) -> str | None:
    try:
        r = requests.get("https://geocoding-api.open-meteo.com/v1/reverse", params={"latitude":lat,"longitude":lon,"count":1}, timeout=10)
        if r.status_code == 200:
            j = r.json()
            results = j.get("results") or []
            if results:
                t = results[0]
                name_parts = [t.get("name"), t.get("admin1"), t.get("country")]
                return ", ".join([p for p in name_parts if p])
    except Exception:
        return None
    return None

def fetch_weather(lat: float, lon: float, dfrom: date, dto: date) -> dict:
    """Fetch daily min/max temps and weathercode + current weather using Open-Meteo.
       Uses archive API if the entire range is in the past; forecast otherwise. Splits if needed.
    """
    today = date.today()
    parts: list[tuple[date,date,str]] = []
    if dto < today:
        parts.append((dfrom, dto, "archive"))
    elif dfrom >= today:
        parts.append((dfrom, dto, "forecast"))
    else:
        parts.append((dfrom, today - timedelta(days=1), "archive"))
        parts.append((today, dto, "forecast"))

    daily_dates: list[str] = []
    tmin: list[float] = []
    tmax: list[float] = []
    wcode: list[int] = []

    for start, end, kind in parts:
        base = "https://archive-api.open-meteo.com/v1/archive" if kind == "archive" else "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": "temperature_2m_min,temperature_2m_max,weathercode",
            "timezone": "auto",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "current_weather": "true" if kind == "forecast" else "false",
        }
        r = requests.get(base, params=params, timeout=20)
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Weather API error ({kind})")
        j = r.json()
        daily = j.get("daily") or {}
        daily_dates += daily.get("time") or []
        tmin += daily.get("temperature_2m_min") or []
        tmax += daily.get("temperature_2m_max") or []
        wcode += daily.get("weathercode") or []
        current = j.get("current_weather") if "current_weather" in j else None

    # Merge into dict
    out = {
        "latitude": lat,
        "longitude": lon,
        "daily": [{"date": d, "tmin_c": mn, "tmax_c": mx, "weathercode": int(wc) if wc is not None else None}
                  for d, mn, mx, wc in zip(daily_dates, tmin, tmax, wcode)]
    }
    # Current weather (try forecast endpoint live)
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": lat,
            "longitude": lon,
            "current_weather": "true",
            "timezone": "auto"
        }, timeout=10)
        if r.status_code == 200:
            out["current_weather"] = r.json().get("current_weather")
    except Exception:
        pass
    return out

# ---------------- Optional APIs ----------------
def wiki_summary(place: str) -> dict | None:
    # naive sanitization
    page = place.strip().replace(" ", "_")
    try:
        r = requests.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{page}", timeout=10)
        if r.status_code == 200:
            j = r.json()
            return {
                "title": j.get("title"),
                "extract": j.get("extract"),
                "url": j.get("content_urls",{}).get("desktop",{}).get("page")
            }
    except Exception:
        return None
    return None

def youtube_search(place: str) -> dict:
    api_key = settings.YOUTUBE_API_KEY
    if api_key:
        # Use official API
        try:
            r = requests.get("https://www.googleapis.com/youtube/v3/search", params={
                "part": "snippet",
                "q": place,
                "type": "video",
                "maxResults": 5,
                "key": api_key
            }, timeout=10)
            if r.status_code == 200:
                j = r.json()
                items = j.get("items", [])
                results = []
                for it in items:
                    vid = it["id"]["videoId"]
                    title = it["snippet"]["title"]
                    results.append({"title": title, "videoId": vid, "url": f"https://www.youtube.com/watch?v={vid}"})
                return {"mode":"api","results": results}
        except Exception:
            pass
    # Fallback to share a search URL
    return {"mode":"link","search_url": f"https://www.youtube.com/results?search_query={requests.utils.quote(place)}"}

def map_image(lat: float, lon: float) -> dict:
    key = settings.GOOGLE_STATIC_MAPS_KEY
    if key:
        url = ("https://maps.googleapis.com/maps/api/staticmap"
               f"?center={lat},{lon}&zoom=10&size=600x300&markers=color:red|{lat},{lon}&key={key}")
        return {"provider":"google_static_maps","url":url}
    # OSM tile (note: tiles are for interactive use; here we provide a link)
    osm = f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=10/{lat}/{lon}"
    return {"provider":"openstreetmap","url": osm}

# ---------------- Routes ----------------

app = FastAPI(title="WeatherWise API", version="1.0")

# CORS
origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
def health():
    return {"ok": True, "time": datetime.utcnow().isoformat()}

@app.post("/api/requests", response_model=RequestOut)
def create_request(payload: CreateRequest, db: Session = Depends(get_db)):
    # Validate range
    clamp_days(payload.date_from, payload.date_to, max_days=31)

    # Geocode
    resolved_name, lat, lon = geocode(payload.location)

    # Fetch weather
    weather = fetch_weather(lat, lon, payload.date_from, payload.date_to)

    # Store
    rec = WeatherRequest(
        location_input=payload.location,
        resolved_name=resolved_name,
        latitude=lat,
        longitude=lon,
        date_from=payload.date_from,
        date_to=payload.date_to,
        weather_json=json.dumps(weather, ensure_ascii=False),
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)

    return RequestOut(
        id=rec.id,
        created_at=rec.created_at,
        location_input=rec.location_input,
        resolved_name=rec.resolved_name,
        latitude=rec.latitude,
        longitude=rec.longitude,
        date_from=rec.date_from,
        date_to=rec.date_to,
        provider=rec.provider,
        weather=json.loads(rec.weather_json),
        notes=rec.notes
    )

@app.get("/api/requests", response_model=list[RequestOut])
def list_requests(limit: int = Query(50, le=500), db: Session = Depends(get_db)):
    rows = db.query(WeatherRequest).order_by(WeatherRequest.id.desc()).limit(limit).all()
    out = []
    for rec in rows:
        out.append(RequestOut(
            id=rec.id,
            created_at=rec.created_at,
            location_input=rec.location_input,
            resolved_name=rec.resolved_name,
            latitude=rec.latitude,
            longitude=rec.longitude,
            date_from=rec.date_from,
            date_to=rec.date_to,
            provider=rec.provider,
            weather=json.loads(rec.weather_json),
            notes=rec.notes
        ))
    return out

@app.get("/api/requests/{rid}", response_model=RequestOut)
def get_request(rid: int, db: Session = Depends(get_db)):
    rec = db.get(WeatherRequest, rid)
    if not rec:
        raise HTTPException(status_code=404, detail="Not found")
    return RequestOut(
        id=rec.id,
        created_at=rec.created_at,
        location_input=rec.location_input,
        resolved_name=rec.resolved_name,
        latitude=rec.latitude,
        longitude=rec.longitude,
        date_from=rec.date_from,
        date_to=rec.date_to,
        provider=rec.provider,
        weather=json.loads(rec.weather_json),
        notes=rec.notes
    )

@app.put("/api/requests/{rid}", response_model=RequestOut)
def update_request(rid: int, patch: UpdateRequest, db: Session = Depends(get_db)):
    rec = db.get(WeatherRequest, rid)
    if not rec:
        raise HTTPException(status_code=404, detail="Not found")
    # compute new values
    new_loc = patch.location if patch.location is not None else rec.location_input
    new_from = patch.date_from if patch.date_from is not None else rec.date_from
    new_to = patch.date_to if patch.date_to is not None else rec.date_to
    if new_to < new_from:
        raise HTTPException(status_code=400, detail="date_to cannot be earlier than date_from")
    clamp_days(new_from, new_to, max_days=31)

    resolved_name, lat, lon = geocode(new_loc)
    weather = fetch_weather(lat, lon, new_from, new_to)

    rec.location_input = new_loc
    rec.resolved_name = resolved_name
    rec.latitude = lat
    rec.longitude = lon
    rec.date_from = new_from
    rec.date_to = new_to
    rec.weather_json = json.dumps(weather, ensure_ascii=False)
    if patch.notes is not None:
        rec.notes = patch.notes
    db.commit()
    db.refresh(rec)
    return RequestOut(
        id=rec.id,
        created_at=rec.created_at,
        location_input=rec.location_input,
        resolved_name=rec.resolved_name,
        latitude=rec.latitude,
        longitude=rec.longitude,
        date_from=rec.date_from,
        date_to=rec.date_to,
        provider=rec.provider,
        weather=json.loads(rec.weather_json),
        notes=rec.notes
    )

@app.delete("/api/requests/{rid}")
def delete_request(rid: int, db: Session = Depends(get_db)):
    rec = db.get(WeatherRequest, rid)
    if not rec:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(rec)
    db.commit()
    return {"deleted": rid}

# ----- Optional: info, media, maps -----
@app.get("/api/info")
def info_for_place(q: str = Query(..., description="Place name for Wikipedia lookup")):
    info = wiki_summary(q)
    if not info:
        raise HTTPException(status_code=404, detail="No info found")
    return info

@app.get("/api/media/youtube")
def media_youtube(q: str):
    return youtube_search(q)

@app.get("/api/map")
def map_for_coords(lat: float, lon: float):
    return map_image(lat, lon)

# ----- Data Export -----
def records_for_export(db: Session):
    rows = db.query(WeatherRequest).order_by(WeatherRequest.id.asc()).all()
    for r in rows:
        yield {
            "id": r.id,
            "created_at": r.created_at.isoformat(),
            "location_input": r.location_input,
            "resolved_name": r.resolved_name,
            "latitude": r.latitude,
            "longitude": r.longitude,
            "date_from": r.date_from.isoformat(),
            "date_to": r.date_to.isoformat(),
            "provider": r.provider,
            "weather": json.loads(r.weather_json),
            "notes": r.notes
        }

@app.get("/api/export")
def export_data(format: str = Query("json", pattern="^(json|csv|md|pdf)$"), db: Session = Depends(get_db)):
    data = list(records_for_export(db))
    if format == "json":
        return JSONResponse(content=data)
    elif format == "csv":
        def gen():
            # flatten a few top-level fields
            fieldnames = ["id","created_at","location_input","resolved_name","latitude","longitude","date_from","date_to","provider","notes"]
            writer = csv.DictWriter(io.StringIO(), fieldnames=fieldnames)
            sio = io.StringIO()
            writer = csv.DictWriter(sio, fieldnames=fieldnames)
            writer.writeheader()
            yield sio.getvalue()
            sio.seek(0); sio.truncate(0)
            for row in data:
                writer.writerow({k: row.get(k) for k in fieldnames})
                yield sio.getvalue()
                sio.seek(0); sio.truncate(0)
        return StreamingResponse(gen(), media_type="text/csv", headers={"Content-Disposition":"attachment; filename=weatherwise.csv"})
    elif format == "md":
        lines = ["# WeatherWise Export", ""]
        for row in data:
            lines.append(f"## Request #{row['id']} — {row['resolved_name']} ({row['latitude']:.4f},{row['longitude']:.4f})")
            lines.append(f"- Entered: **{row['location_input']}**")
            lines.append(f"- Range: **{row['date_from']} → {row['date_to']}**")
            lines.append("")
            lines.append("| Date | Tmin (°C) | Tmax (°C) | Code |")
            lines.append("|---|---:|---:|---:|")
            for d in row["weather"].get("daily", []):
                lines.append(f"| {d['date']} | {d['tmin_c']} | {d['tmax_c']} | {d.get('weathercode','')} |")
            lines.append("")
        md = "\n".join(lines)
        return PlainTextResponse(md, media_type="text/markdown")
    else:  # pdf
        # Generate a very simple tabular PDF using reportlab
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import inch

        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=letter)
        width, height = letter

        for idx, row in enumerate(data, start=1):
            c.setFont("Helvetica-Bold", 14)
            c.drawString(72, height - 72, f"WeatherWise Export — Request #{row['id']}")
            c.setFont("Helvetica", 10)
            c.drawString(72, height - 90, f"{row['resolved_name']} ({row['latitude']:.4f},{row['longitude']:.4f})")
            c.drawString(72, height - 105, f"Range: {row['date_from']} → {row['date_to']}")
            y = height - 130
            c.setFont("Helvetica-Bold", 10)
            c.drawString(72, y, "Date")
            c.drawString(180, y, "Tmin (°C)")
            c.drawString(270, y, "Tmax (°C)")
            c.drawString(360, y, "Code")
            y -= 14
            c.setFont("Helvetica", 10)
            for d in row["weather"].get("daily", []):
                if y < 72:
                    c.showPage()
                    y = height - 72
                c.drawString(72, y, str(d["date"]))
                c.drawRightString(240, y, f"{d['tmin_c']}")
                c.drawRightString(330, y, f"{d['tmax_c']}")
                c.drawRightString(420, y, str(d.get("weathercode","")))
                y -= 12
            c.showPage()
        c.save()
        buffer.seek(0)
        return StreamingResponse(buffer, media_type="application/pdf", headers={"Content-Disposition":"attachment; filename=weatherwise.pdf"})

from fastapi.staticfiles import StaticFiles
import os

frontend_dir = os.path.join(os.path.dirname(__file__), "../frontend")
app.mount("/ui", StaticFiles(directory=frontend_dir, html=True), name="ui")

# Root helpful message
@app.get("/")
def root():
    return {
        "name": "WeatherWise API",
        "message": "Use /api/requests (POST) to fetch & store weather. See README for details."
    }
