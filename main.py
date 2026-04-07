"""
Amri Maintenance Tracker — Cloud Edition
FastAPI entry point with auth + WebSocket real-time sync.
"""
import os, json, secrets, hashlib, asyncio, logging
from datetime import datetime, date as dt_date
from typing import Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import init_db, SessionLocal, get_db
from models import User, Pump, WellInfo, GlobalSettings
from api import router as api_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

APP_VERSION = "4.1.0"
APP_NAME = "Amri Maintenance Tracker"

app = FastAPI(title=APP_NAME, version=APP_VERSION)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

# ── Simple token store (in-memory, survives until restart) ─────
active_sessions: dict[str, dict] = {}  # token → {username, role}

# ── WebSocket manager ─────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.connections: Set[WebSocket] = set()
    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.add(ws)
    def disconnect(self, ws: WebSocket):
        self.connections.discard(ws)
    async def broadcast(self, message: dict):
        dead = []
        for ws in self.connections:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.connections.discard(ws)

ws_manager = ConnectionManager()

# ── Auth schemas ──────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    pin: str

# ── Auth endpoints ────────────────────────────────────────────
@app.post("/api/auth/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == req.username.lower().strip()).first()
    if not user or user.pin != req.pin.strip():
        raise HTTPException(401, "Invalid username or PIN")
    token = secrets.token_hex(16)
    active_sessions[token] = {"username": user.username, "role": user.role}
    return {"token": token, "username": user.username, "role": user.role}

@app.post("/api/auth/logout")
def logout(request: Request):
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    active_sessions.pop(token, None)
    return {"message": "Logged out"}

@app.get("/api/auth/me")
def auth_me(request: Request):
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    session = active_sessions.get(token)
    if not session:
        raise HTTPException(401, "Not authenticated")
    return session

# ── WebSocket endpoint ────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            await ws.receive_text()  # keep alive
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)

# ── Broadcast hook — middleware to notify clients after mutations ──
@app.middleware("http")
async def broadcast_mutations(request: Request, call_next):
    response = await call_next(request)
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and response.status_code < 400:
        if "/api/" in str(request.url) and "/auth/" not in str(request.url):
            asyncio.create_task(ws_manager.broadcast({
                "type": "refresh", "timestamp": datetime.utcnow().isoformat()
            }))
    return response

# ── API routes ────────────────────────────────────────────────
app.include_router(api_router)

# ── Server info ──────────────────────────────────────────────
@app.get("/api/server-info")
def server_info():
    from database import DATABASE_URL
    is_pg = "postgresql" in DATABASE_URL
    return {
        "app_name": APP_NAME, "version": APP_VERSION,
        "database": "PostgreSQL" if is_pg else "SQLite",
        "host": "cloud", "port": 0,
        "url": "cloud-hosted",
    }

# ── Static files + SPA fallback ──────────────────────────────
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
def serve_index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

# ── Startup ──────────────────────────────────────────────────
@app.on_event("startup")
def on_startup():
    init_db()
    _seed_if_empty()

def _seed_if_empty():
    db = SessionLocal()
    try:
        # Create default users
        if db.query(User).count() == 0:
            db.add(User(username="admin", pin="1234", role="admin"))
            db.add(User(username="operator", pin="1234", role="operator"))
            db.commit()
        # Create global settings
        if db.query(GlobalSettings).count() == 0:
            db.add(GlobalSettings())
            db.commit()
        # Seed pumps if empty
        if db.query(Pump).count() == 0:
            _seed_pumps(db)
    finally:
        db.close()

def _seed_pumps(db):
    from datetime import date
    pumps = [
        (1,"TH-55","HP-25",'GD-4"',13,date(2026,3,15),95,141,157,156,95,"Oil",""),
        (2,"TH-55","HP-58",'GD-4"',29,date(2026,3,16),113,78,29,29,29,"Oil",""),
        (3,"TH-55","HP-63",'GD-4"',32,date(2026,3,16),118,97,32,148,220,"Oil",""),
        (4,"TH-55","HP-15",'GD-4"',1,date(2026,3,18),93,140,156,166,166,"Oil",""),
        (5,"TH-55","HP-87",'GD-4"',41,date(2026,3,16),90,90,90,90,90,"Grease","New FE"),
        (6,"TH-55","HP-74",'GD-4"',27,date(2026,3,14),27,27,72,102,27,"Oil",""),
        (7,"TH-55","HP-94",'GD-4"',35,date(2026,3,16),45,69,45,35,45,"Grease",""),
        (8,"TH-55","HP-42",'GD-4"',32,date(2026,3,16),171,171,171,171,171,"Oil","Lock FE"),
        (9,"TH-55","HP-97",'GD-4"',21,date(2026,3,17),160,160,160,160,160,"Grease",""),
        (10,"TH-55","HP-21",'GD-4"',42,date(2026,3,16),42,143,42,42,157,"Oil",""),
        (11,"TH-55","HP-60",'GD-4"',12,date(2026,3,18),150,150,150,150,27,"Oil",""),
        (12,"TH-55","HP-131",'GD-4"',35,date(2026,3,16),171,171,171,171,171,"Grease",""),
    ]
    for stn,trailer,name,model,stages,insp,h1,h2,h3,h4,h5,grease,notes in pumps:
        db.add(Pump(station=stn,trailer=trailer,pump_name=name,model=model,
                    status="Active",total_stages=stages,packing_count=stages,
                    inspection_date=insp,hole_1_count=h1,hole_2_count=h2,
                    hole_3_count=h3,hole_4_count=h4,hole_5_count=h5,
                    grease_type=grease,notes=notes))
    db.commit()

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
