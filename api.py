"""
FastAPI API router — all maintenance tracker endpoints.
v3.1 — Replace pump, reorder, color overrides, packing removed from stages.
"""
import json
import logging
from datetime import datetime, date as dt_date
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from io import BytesIO

from database import get_db
from models import Pump, HistoryLog, WellInfo, GlobalSettings, User

# Excel/PDF optional
try:
    from excel_handler import import_from_excel, export_to_excel
    HAS_EXCEL = True
except ImportError:
    HAS_EXCEL = False
try:
    from pdf_report import generate_pdf_report
    HAS_PDF = True
except ImportError:
    HAS_PDF = False

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


# ── Pydantic Schemas ────────────────────────────────────────────────

class OperatorAction(BaseModel):
    operator_name: str

class AddStageRequest(OperatorAction):
    comment: str = ""

class ChangeSeatValveRequest(OperatorAction):
    comment: str = ""

class ChangePackingRequest(OperatorAction):
    comment: str = ""

class ResetHoleRequest(OperatorAction):
    hole_number: int  # 1-5
    comment: str = ""

class StatusChangeRequest(OperatorAction):
    status: str
    comment: str = ""

class PumpUpdateRequest(OperatorAction):
    grease_type: Optional[str] = None
    inspection_date: Optional[str] = None
    notes: Optional[str] = None
    trailer: Optional[str] = None
    model: Optional[str] = None
    comment: str = ""

class WellInfoUpdate(BaseModel):
    well_name: Optional[str] = None
    location: Optional[str] = None
    rig_name: Optional[str] = None
    pad_name: Optional[str] = None
    max_stages_vs: Optional[int] = None

class AddStageSelectedRequest(OperatorAction):
    pump_ids: List[int]
    comment: str = ""

class ManualEditRequest(OperatorAction):
    """Manual edit for any pump fields, including pump_name."""
    pump_name: Optional[str] = None
    total_stages: Optional[int] = None
    packing_count: Optional[int] = None
    hole_1_count: Optional[int] = None
    hole_2_count: Optional[int] = None
    hole_3_count: Optional[int] = None
    hole_4_count: Optional[int] = None
    hole_5_count: Optional[int] = None

    status: Optional[str] = None
    grease_type: Optional[str] = None
    inspection_date: Optional[str] = None
    notes: Optional[str] = None
    comment: str = ""

class ResetCountersRequest(OperatorAction):
    reset_packing: bool = False
    reset_holes: bool = False
    reset_stages: bool = False
    comment: str = ""

class FullResetRequest(OperatorAction):
    comment: str = ""

class UpdateLimitsRequest(OperatorAction):
    packing_warn: Optional[int] = None
    packing_crit: Optional[int] = None
    hole_warn: Optional[int] = None
    hole_limit: Optional[int] = None
    sv_warn: Optional[int] = None
    sv_limit: Optional[int] = None
    stage_warn: Optional[int] = None
    stage_crit: Optional[int] = None
    comment: str = ""

class UpdateSettingsRequest(OperatorAction):
    packing_warn_default: Optional[int] = None
    packing_crit_default: Optional[int] = None
    hole_warn_default: Optional[int] = None
    hole_limit_default: Optional[int] = None
    sv_warn_default: Optional[int] = None
    sv_limit_default: Optional[int] = None
    stage_warn_default: Optional[int] = None
    stage_crit_default: Optional[int] = None
    apply_to_all: bool = False
    comment: str = ""

class UndoRequest(OperatorAction):
    pass

class RenamePumpRequest(OperatorAction):
    new_name: str
    comment: str = ""

class RemovePumpRequest(OperatorAction):
    reason: str = ""
    comment: str = ""

class RestorePumpRequest(OperatorAction):
    comment: str = ""

class CreatePumpRequest(OperatorAction):
    """Create a brand-new pump entry."""
    pump_name: str
    station: Optional[int] = None
    model: str = ""
    trailer: str = ""
    status: str = "Active"
    grease_type: str = "Oil"
    total_stages: int = 0
    packing_count: int = 0
    hole_1_count: int = 0
    hole_2_count: int = 0
    hole_3_count: int = 0
    hole_4_count: int = 0
    hole_5_count: int = 0
    inspection_date: Optional[str] = None
    notes: str = ""
    packing_warn: int = 140
    packing_crit: int = 160
    hole_limit: int = 45
    sv_limit: int = 45
    group_name: str = "Unassigned"
    comment: str = ""

class ChangeGroupRequest(OperatorAction):
    """Change a pump's group assignment."""
    group_name: str  # Group A / B / C / D / Unassigned
    comment: str = ""

class ChangeGroupBulkRequest(OperatorAction):
    """Change multiple pumps' group assignments."""
    pump_ids: List[int]
    group_name: str
    comment: str = ""

class ReplacePumpRequest(OperatorAction):
    """Replace an existing pump with a new one."""
    new_pump_name: str
    model: str = ""
    status: str = "Active"
    grease_type: str = "Oil"
    total_stages: int = 0
    hole_1_count: int = 0
    hole_2_count: int = 0
    hole_3_count: int = 0
    hole_4_count: int = 0
    hole_5_count: int = 0
    inspection_date: Optional[str] = None
    notes: str = ""
    group_name: str = "Unassigned"
    reason: str = ""
    comment: str = ""

class SetColorRequest(OperatorAction):
    """Set manual color override on a cell."""
    field: str  # stages, hole_1..hole_5
    color: Optional[str] = None  # green/yellow/red or null to clear
    comment: str = ""

# ── Helper ──────────────────────────────────────────────────────────

def _log_action(db: Session, operator: str, pump: Pump, action: str,
                before: str, after: str, comment: str = ""):
    log = HistoryLog(
        operator_name=operator,
        pump_id=pump.id if pump else None,
        pump_name=pump.pump_name if pump else "",
        action_type=action,
        before_value=before,
        after_value=after,
        comment=comment,
    )
    db.add(log)


def _log_bulk_action(db: Session, operator: str, action: str,
                     count: int, comment: str = ""):
    log = HistoryLog(
        operator_name=operator,
        pump_id=None,
        pump_name=f"{count} pumps",
        action_type=action,
        before_value="",
        after_value=f"{count} pumps updated",
        comment=comment,
    )
    db.add(log)


# ── Pump Endpoints ──────────────────────────────────────────────────

@router.get("/pumps")
def list_pumps(db: Session = Depends(get_db)):
    """List all in-service pumps."""
    pumps = db.query(Pump).filter(
        (Pump.fleet_state == "In Service") | (Pump.fleet_state.is_(None))
    ).order_by(Pump.sort_order, Pump.station).all()
    return [p.to_dict() for p in pumps]


@router.get("/pumps/removed")
def list_removed_pumps(db: Session = Depends(get_db)):
    """List all removed pumps."""
    pumps = db.query(Pump).filter(
        Pump.fleet_state == "Removed"
    ).order_by(Pump.removed_date.desc()).all()
    return [p.to_dict() for p in pumps]


# ══════════════════════════════════════════════════════════════════════
# CREATE PUMP — dynamic pump addition
# ══════════════════════════════════════════════════════════════════════

@router.post("/pumps")
def create_pump(req: CreatePumpRequest, db: Session = Depends(get_db)):
    """Create a brand-new pump entry with optional carried-over values."""
    name = req.pump_name.strip()
    if not name:
        raise HTTPException(400, "Pump name is required")

    # Check duplicate name among active pumps
    existing = db.query(Pump).filter(
        Pump.pump_name == name,
        (Pump.fleet_state == "In Service") | (Pump.fleet_state.is_(None))
    ).first()
    if existing:
        raise HTTPException(400, f"Pump name '{name}' already exists in the active fleet")

    # Validate status
    valid_status = {"Active", "Standby", "Down", "Maintenance"}
    if req.status not in valid_status:
        raise HTTPException(400, f"Status must be one of: {valid_status}")

    # Validate grease
    if req.grease_type not in {"Oil", "Grease"}:
        raise HTTPException(400, "Grease type must be 'Oil' or 'Grease'")

    # Validate numerics
    numeric_fields = {
        "total_stages": req.total_stages, "packing_count": req.packing_count,
        "hole_1_count": req.hole_1_count, "hole_2_count": req.hole_2_count,
        "hole_3_count": req.hole_3_count, "hole_4_count": req.hole_4_count,
        "hole_5_count": req.hole_5_count,
    }
    for field, value in numeric_fields.items():
        if value < 0:
            raise HTTPException(400, f"{field} cannot be negative")

    # Auto-assign station if not provided
    station = req.station
    if station is None or station <= 0:
        max_station = db.query(Pump.station).order_by(Pump.station.desc()).first()
        station = (max_station[0] + 1) if max_station else 1

    # Parse inspection date
    insp_date = None
    if req.inspection_date:
        try:
            insp_date = dt_date.fromisoformat(req.inspection_date)
        except ValueError:
            raise HTTPException(400, "Invalid inspection date. Use YYYY-MM-DD")

    pump = Pump(
        pump_name=name,
        station=station,
        model=req.model or "",
        trailer=req.trailer or "",
        status=req.status,
        grease_type=req.grease_type,
        total_stages=req.total_stages,
        packing_count=req.packing_count,
        hole_1_count=req.hole_1_count,
        hole_2_count=req.hole_2_count,
        hole_3_count=req.hole_3_count,
        hole_4_count=req.hole_4_count,
        hole_5_count=req.hole_5_count,
        inspection_date=insp_date,
        notes=req.notes or "",
        packing_warn=req.packing_warn,
        packing_crit=req.packing_crit,
        hole_limit=req.hole_limit,
        sv_limit=req.sv_limit,
        fleet_state="In Service",
        group_name=req.group_name or "Unassigned",
        last_updated=datetime.utcnow(),
    )
    db.add(pump)
    db.flush()  # get pump.id before logging

    after = json.dumps(pump.snapshot())
    _log_action(db, req.operator_name, pump, "Add Pump",
                "", after, req.comment or f"New pump: {name}")
    db.commit()

    return {"message": f"Pump '{name}' created (station {station})", "pump": pump.to_dict()}


# ══════════════════════════════════════════════════════════════════════
# PUMP GROUPS / LOAD DISTRIBUTION
# (Static routes MUST be before /pumps/{pump_id} dynamic route)
# ══════════════════════════════════════════════════════════════════════

VALID_GROUPS = {"Group A", "Group B", "Group C", "Group D", "Unassigned"}


@router.get("/pumps/group-summary")
def group_summary(db: Session = Depends(get_db)):
    """Return per-group statistics for load distribution analysis with recommendations."""
    in_service = db.query(Pump).filter(
        (Pump.fleet_state == "In Service") | (Pump.fleet_state.is_(None))
    ).all()

    groups = {}
    all_stages = []
    group_names = ["Group A", "Group B", "Group C", "Group D", "Unassigned"]

    for gname in group_names:
        members = [p for p in in_service if (p.group_name or "Unassigned") == gname]
        active = [p for p in members if p.status == "Active"]
        total_stages = sum(p.total_stages for p in members)
        total_packing = sum(p.packing_count for p in members)
        n = len(members)

        # Per-pump max hole counts
        hole_maxes = [max(p.hole_1_count, p.hole_2_count, p.hole_3_count,
                         p.hole_4_count, p.hole_5_count) for p in members] if members else [0]
        highest_hole = max(hole_maxes) if hole_maxes else 0

        # Highest S&V (seat valve = same as hole counts in this system)
        highest_sv = highest_hole

        # Highest packing in group
        highest_packing = max((p.packing_count for p in members), default=0)

        # Identify the pump with the highest warning
        highest_warning_pump = ""
        if members:
            worst = max(members, key=lambda p: p.packing_count + max(
                p.hole_1_count, p.hole_2_count, p.hole_3_count, p.hole_4_count, p.hole_5_count))
            highest_warning_pump = worst.pump_name

        alert_count = sum(
            1 for p in members
            if (p.packing_count > (p.packing_warn or 140))
            or max(p.hole_1_count, p.hole_2_count, p.hole_3_count,
                   p.hole_4_count, p.hole_5_count) >= (p.hole_limit or 45)
        )

        groups[gname] = {
            "total": n,
            "active": len(active),
            "standby": len([p for p in members if p.status == "Standby"]),
            "down": len([p for p in members if p.status == "Down"]),
            "maintenance": len([p for p in members if p.status == "Maintenance"]),
            "total_stages": total_stages,
            "avg_stages": round(total_stages / n, 1) if n else 0,
            "total_packing": total_packing,
            "avg_packing": round(total_packing / n, 1) if n else 0,
            "highest_sv": highest_sv,
            "highest_hole": highest_hole,
            "highest_packing": highest_packing,
            "highest_warning_pump": highest_warning_pump,
            "alerts": alert_count,
            "pumps": [p.pump_name for p in members],
        }
        all_stages.append(total_stages)

    # Balance indicator: overloaded / underused / balanced / empty
    active_groups = {g: s for g, s in zip(group_names, all_stages) if groups[g]["total"] > 0}
    if active_groups:
        avg_all = sum(active_groups.values()) / len(active_groups) if active_groups else 1
        for gname, stats in groups.items():
            if stats["total"] == 0:
                stats["balance"] = "empty"
            elif stats["total_stages"] > avg_all * 1.25:
                stats["balance"] = "overloaded"
            elif stats["total_stages"] < avg_all * 0.75 and avg_all > 0:
                stats["balance"] = "underused"
            else:
                stats["balance"] = "balanced"
    else:
        for g in groups.values():
            g["balance"] = "empty" if g["total"] == 0 else "balanced"

    # Recommendations
    recommendation = {"next_group": "", "rotate_pump": "", "rotate_from": ""}
    if active_groups:
        # Suggest next group = group with fewest stages
        lightest = min(active_groups, key=lambda g: active_groups[g])
        recommendation["next_group"] = lightest
        # Suggest pump to rotate out = pump with highest load in heaviest group
        heaviest = max(active_groups, key=lambda g: active_groups[g])
        recommendation["rotate_from"] = heaviest
        if groups[heaviest]["highest_warning_pump"]:
            recommendation["rotate_pump"] = groups[heaviest]["highest_warning_pump"]

    return {"groups": groups, "recommendation": recommendation}


# ── Global Settings ─────────────────────────────────────────────────

def _get_or_create_settings(db: Session) -> GlobalSettings:
    settings = db.query(GlobalSettings).first()
    if not settings:
        settings = GlobalSettings()
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    """Get global default limits."""
    settings = _get_or_create_settings(db)
    return settings.to_dict()


@router.post("/settings")
def update_settings(req: UpdateSettingsRequest, db: Session = Depends(get_db)):
    """Update global default limits with optional apply-to-all."""
    settings = _get_or_create_settings(db)
    changes = []
    fields = [
        ("packing_warn_default", req.packing_warn_default),
        ("packing_crit_default", req.packing_crit_default),
        ("hole_warn_default", req.hole_warn_default),
        ("hole_limit_default", req.hole_limit_default),
        ("sv_warn_default", req.sv_warn_default),
        ("sv_limit_default", req.sv_limit_default),
        ("stage_warn_default", req.stage_warn_default),
        ("stage_crit_default", req.stage_crit_default),
    ]
    for field_name, value in fields:
        if value is not None:
            old = getattr(settings, field_name)
            setattr(settings, field_name, value)
            changes.append(f"{field_name}: {old} → {value}")
    settings.last_updated = datetime.utcnow()
    settings.updated_by = req.operator_name

    # Log the settings change
    log = HistoryLog(
        operator_name=req.operator_name,
        pump_id=None,
        pump_name="SYSTEM",
        action_type="Update Global Settings",
        before_value="",
        after_value="; ".join(changes) if changes else "No changes",
        comment=req.comment or "",
    )
    db.add(log)

    # Optionally apply to all pumps
    applied_count = 0
    if req.apply_to_all:
        pump_field_map = {
            "packing_warn_default": "packing_warn",
            "packing_crit_default": "packing_crit",
            "hole_warn_default": "hole_warn",
            "hole_limit_default": "hole_limit",
            "sv_warn_default": "sv_warn",
            "sv_limit_default": "sv_limit",
            "stage_warn_default": "stage_warn",
            "stage_crit_default": "stage_crit",
        }
        all_pumps = db.query(Pump).filter(
            (Pump.fleet_state == "In Service") | (Pump.fleet_state.is_(None))
        ).all()
        for pump in all_pumps:
            for settings_field, pump_field in pump_field_map.items():
                setattr(pump, pump_field, getattr(settings, settings_field))
            pump.last_updated = datetime.utcnow()
            applied_count += 1
        if applied_count:
            apply_log = HistoryLog(
                operator_name=req.operator_name,
                pump_id=None,
                pump_name=f"{applied_count} pumps",
                action_type="Apply Global Limits",
                before_value="",
                after_value=f"Applied defaults to {applied_count} pumps",
                comment="",
            )
            db.add(apply_log)

    db.commit()
    return {
        "message": f"Settings updated" + (f", applied to {applied_count} pumps" if req.apply_to_all else ""),
        "settings": settings.to_dict(),
    }


@router.post("/pumps/change-group-bulk")
def change_group_bulk(req: ChangeGroupBulkRequest, db: Session = Depends(get_db)):
    """Move multiple pumps to a group at once."""
    if req.group_name not in VALID_GROUPS:
        raise HTTPException(400, f"Group must be one of: {VALID_GROUPS}")
    if not req.pump_ids:
        raise HTTPException(400, "No pumps selected")

    updated = 0
    for pid in req.pump_ids:
        pump = db.query(Pump).filter(Pump.id == pid).first()
        if pump:
            before = json.dumps(pump.snapshot())
            old_group = pump.group_name or "Unassigned"
            pump.group_name = req.group_name
            pump.last_updated = datetime.utcnow()
            _log_action(db, req.operator_name, pump, "Change Group",
                        before, json.dumps(pump.snapshot()),
                        req.comment or f"{old_group} → {req.group_name}")
            updated += 1

    db.commit()
    return {"message": f"{updated} pump(s) moved to {req.group_name}"}


# ── Single-pump lookup (dynamic route — must come after static /pumps/... routes)

@router.get("/pumps/{pump_id}")
def get_pump(pump_id: int, db: Session = Depends(get_db)):
    pump = db.query(Pump).filter(Pump.id == pump_id).first()
    if not pump:
        raise HTTPException(404, "Pump not found")
    return pump.to_dict()


@router.post("/pumps/{pump_id}/change-group")
def change_pump_group(pump_id: int, req: ChangeGroupRequest, db: Session = Depends(get_db)):
    """Assign a pump to a different group."""
    if req.group_name not in VALID_GROUPS:
        raise HTTPException(400, f"Group must be one of: {VALID_GROUPS}")

    pump = db.query(Pump).filter(Pump.id == pump_id).first()
    if not pump:
        raise HTTPException(404, "Pump not found")

    before = json.dumps(pump.snapshot())
    old_group = pump.group_name or "Unassigned"
    pump.group_name = req.group_name
    pump.last_updated = datetime.utcnow()

    _log_action(db, req.operator_name, pump, "Change Group",
                before, json.dumps(pump.snapshot()),
                req.comment or f"{old_group} → {req.group_name}")
    db.commit()
    return {"message": f"{pump.pump_name} moved to {req.group_name}", "pump": pump.to_dict()}


# ══════════════════════════════════════════════════════════════════════
# ADD STAGE
# ══════════════════════════════════════════════════════════════════════

@router.post("/pumps/{pump_id}/add-stage")
def add_stage_single(pump_id: int, req: AddStageRequest, db: Session = Depends(get_db)):
    pump = db.query(Pump).filter(Pump.id == pump_id).first()
    if not pump:
        raise HTTPException(404, "Pump not found")
    before = json.dumps(pump.snapshot())
    pump.total_stages += 1
    pump.hole_1_count += 1
    pump.hole_2_count += 1
    pump.hole_3_count += 1
    pump.hole_4_count += 1
    pump.hole_5_count += 1
    pump.last_updated = datetime.utcnow()
    after = json.dumps(pump.snapshot())
    _log_action(db, req.operator_name, pump, "Add Stage", before, after, req.comment)
    db.commit()
    return pump.to_dict()


@router.post("/pumps/add-stage-active")
def add_stage_active(req: AddStageRequest, db: Session = Depends(get_db)):
    pumps = db.query(Pump).filter(
        Pump.status == "Active",
        (Pump.fleet_state == "In Service") | (Pump.fleet_state.is_(None))
    ).all()
    count = 0
    for pump in pumps:
        pump.total_stages += 1
        pump.hole_1_count += 1
        pump.hole_2_count += 1
        pump.hole_3_count += 1
        pump.hole_4_count += 1
        pump.hole_5_count += 1
        pump.last_updated = datetime.utcnow()
        count += 1
    _log_bulk_action(db, req.operator_name, "Add Stage (Active Only)", count, req.comment)
    db.commit()
    return {"message": f"Added stage to {count} active pumps", "count": count}


@router.post("/pumps/add-stage-all")
def add_stage_all(req: AddStageRequest, db: Session = Depends(get_db)):
    pumps = db.query(Pump).filter(
        (Pump.fleet_state == "In Service") | (Pump.fleet_state.is_(None))
    ).all()
    count = 0
    for pump in pumps:
        pump.total_stages += 1
        pump.hole_1_count += 1
        pump.hole_2_count += 1
        pump.hole_3_count += 1
        pump.hole_4_count += 1
        pump.hole_5_count += 1
        pump.last_updated = datetime.utcnow()
        count += 1
    _log_bulk_action(db, req.operator_name, "Add Stage (All)", count, req.comment)
    db.commit()
    return {"message": f"Added stage to {count} pumps", "count": count}


@router.post("/pumps/add-stage-selected")
def add_stage_selected(req: AddStageSelectedRequest, db: Session = Depends(get_db)):
    pumps = db.query(Pump).filter(Pump.id.in_(req.pump_ids)).all()
    count = 0
    for pump in pumps:
        pump.total_stages += 1
        pump.hole_1_count += 1
        pump.hole_2_count += 1
        pump.hole_3_count += 1
        pump.hole_4_count += 1
        pump.hole_5_count += 1
        pump.last_updated = datetime.utcnow()
        count += 1
    _log_bulk_action(db, req.operator_name, "Add Stage (Selected)", count, req.comment)
    db.commit()
    return {"message": f"Added stage to {count} selected pumps", "count": count}


# ══════════════════════════════════════════════════════════════════════
# SUBTRACT STAGE (-1)
# ══════════════════════════════════════════════════════════════════════

@router.post("/pumps/{pump_id}/subtract-stage")
def subtract_stage_single(pump_id: int, req: AddStageRequest, db: Session = Depends(get_db)):
    pump = db.query(Pump).filter(Pump.id == pump_id).first()
    if not pump:
        raise HTTPException(404, "Pump not found")
    before = json.dumps(pump.snapshot())
    pump.total_stages = max(0, pump.total_stages - 1)
    pump.hole_1_count = max(0, pump.hole_1_count - 1)
    pump.hole_2_count = max(0, pump.hole_2_count - 1)
    pump.hole_3_count = max(0, pump.hole_3_count - 1)
    pump.hole_4_count = max(0, pump.hole_4_count - 1)
    pump.hole_5_count = max(0, pump.hole_5_count - 1)
    pump.last_updated = datetime.utcnow()
    after = json.dumps(pump.snapshot())
    _log_action(db, req.operator_name, pump, "Subtract Stage", before, after, req.comment)
    db.commit()
    return pump.to_dict()


@router.post("/pumps/subtract-stage-active")
def subtract_stage_active(req: AddStageRequest, db: Session = Depends(get_db)):
    pumps = db.query(Pump).filter(
        Pump.status == "Active",
        (Pump.fleet_state == "In Service") | (Pump.fleet_state.is_(None))
    ).all()
    count = 0
    for pump in pumps:
        pump.total_stages = max(0, pump.total_stages - 1)
        pump.hole_1_count = max(0, pump.hole_1_count - 1)
        pump.hole_2_count = max(0, pump.hole_2_count - 1)
        pump.hole_3_count = max(0, pump.hole_3_count - 1)
        pump.hole_4_count = max(0, pump.hole_4_count - 1)
        pump.hole_5_count = max(0, pump.hole_5_count - 1)
        pump.last_updated = datetime.utcnow()
        count += 1
    _log_bulk_action(db, req.operator_name, "Subtract Stage (Active Only)", count, req.comment)
    db.commit()
    return {"message": f"Subtracted stage from {count} active pumps", "count": count}


@router.post("/pumps/subtract-stage-all")
def subtract_stage_all(req: AddStageRequest, db: Session = Depends(get_db)):
    pumps = db.query(Pump).filter(
        (Pump.fleet_state == "In Service") | (Pump.fleet_state.is_(None))
    ).all()
    count = 0
    for pump in pumps:
        pump.total_stages = max(0, pump.total_stages - 1)
        pump.hole_1_count = max(0, pump.hole_1_count - 1)
        pump.hole_2_count = max(0, pump.hole_2_count - 1)
        pump.hole_3_count = max(0, pump.hole_3_count - 1)
        pump.hole_4_count = max(0, pump.hole_4_count - 1)
        pump.hole_5_count = max(0, pump.hole_5_count - 1)
        pump.last_updated = datetime.utcnow()
        count += 1
    _log_bulk_action(db, req.operator_name, "Subtract Stage (All)", count, req.comment)
    db.commit()
    return {"message": f"Subtracted stage from {count} pumps", "count": count}


# ══════════════════════════════════════════════════════════════════════
# SEAT & VALVE / PACKING
# ══════════════════════════════════════════════════════════════════════

@router.post("/pumps/{pump_id}/change-seat-valve")
def change_seat_valve(pump_id: int, req: ChangeSeatValveRequest, db: Session = Depends(get_db)):
    pump = db.query(Pump).filter(Pump.id == pump_id).first()
    if not pump:
        raise HTTPException(404, "Pump not found")
    before = json.dumps(pump.snapshot())
    pump.hole_1_count = 0
    pump.hole_2_count = 0
    pump.hole_3_count = 0
    pump.hole_4_count = 0
    pump.hole_5_count = 0
    pump.last_updated = datetime.utcnow()
    after = json.dumps(pump.snapshot())
    _log_action(db, req.operator_name, pump, "Change Seat & Valve", before, after, req.comment)
    db.commit()
    return pump.to_dict()


@router.post("/pumps/{pump_id}/change-packing")
def change_packing(pump_id: int, req: ChangePackingRequest, db: Session = Depends(get_db)):
    pump = db.query(Pump).filter(Pump.id == pump_id).first()
    if not pump:
        raise HTTPException(404, "Pump not found")
    before = json.dumps(pump.snapshot())
    pump.packing_count = 0
    pump.last_updated = datetime.utcnow()
    after = json.dumps(pump.snapshot())
    _log_action(db, req.operator_name, pump, "Change Packing", before, after, req.comment)
    db.commit()
    return pump.to_dict()


# ══════════════════════════════════════════════════════════════════════
# HOLE RESET
# ══════════════════════════════════════════════════════════════════════

@router.post("/pumps/{pump_id}/reset-hole")
def reset_hole(pump_id: int, req: ResetHoleRequest, db: Session = Depends(get_db)):
    pump = db.query(Pump).filter(Pump.id == pump_id).first()
    if not pump:
        raise HTTPException(404, "Pump not found")
    if req.hole_number not in range(1, 6):
        raise HTTPException(400, "hole_number must be 1-5")
    before = json.dumps(pump.snapshot())
    field = f"hole_{req.hole_number}_count"
    setattr(pump, field, 0)
    pump.last_updated = datetime.utcnow()
    after = json.dumps(pump.snapshot())
    _log_action(db, req.operator_name, pump, f"Reset Hole {req.hole_number}", before, after, req.comment)
    db.commit()
    return pump.to_dict()


# ══════════════════════════════════════════════════════════════════════
# STATUS CHANGE
# ══════════════════════════════════════════════════════════════════════

@router.post("/pumps/{pump_id}/status")
def change_status(pump_id: int, req: StatusChangeRequest, db: Session = Depends(get_db)):
    pump = db.query(Pump).filter(Pump.id == pump_id).first()
    if not pump:
        raise HTTPException(404, "Pump not found")
    valid = {"Active", "Standby", "Down", "Maintenance"}
    if req.status not in valid:
        raise HTTPException(400, f"Status must be one of: {valid}")
    before = json.dumps(pump.snapshot())
    pump.status = req.status
    pump.last_updated = datetime.utcnow()
    after = json.dumps(pump.snapshot())
    _log_action(db, req.operator_name, pump, "Status Change", before, after, req.comment)
    db.commit()
    return pump.to_dict()


# ══════════════════════════════════════════════════════════════════════
# UPDATE INFO (grease, date, notes, trailer, model)
# ══════════════════════════════════════════════════════════════════════

@router.post("/pumps/{pump_id}/update")
def update_pump(pump_id: int, req: PumpUpdateRequest, db: Session = Depends(get_db)):
    pump = db.query(Pump).filter(Pump.id == pump_id).first()
    if not pump:
        raise HTTPException(404, "Pump not found")
    before = json.dumps(pump.snapshot())
    if req.grease_type is not None:
        pump.grease_type = req.grease_type
    if req.inspection_date is not None:
        try:
            pump.inspection_date = dt_date.fromisoformat(req.inspection_date)
        except ValueError:
            raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD")
    if req.notes is not None:
        pump.notes = req.notes
    if req.trailer is not None:
        pump.trailer = req.trailer
    if req.model is not None:
        pump.model = req.model
    pump.last_updated = datetime.utcnow()
    after = json.dumps(pump.snapshot())
    _log_action(db, req.operator_name, pump, "Update Info", before, after, req.comment)
    db.commit()
    return pump.to_dict()


# ══════════════════════════════════════════════════════════════════════
# MANUAL EDIT — set any field to an exact value (including pump_name)
# ══════════════════════════════════════════════════════════════════════

@router.post("/pumps/{pump_id}/manual-edit")
def manual_edit(pump_id: int, req: ManualEditRequest, db: Session = Depends(get_db)):
    pump = db.query(Pump).filter(Pump.id == pump_id).first()
    if not pump:
        raise HTTPException(404, "Pump not found")

    before = json.dumps(pump.snapshot())
    changes = []

    # Pump name rename
    if req.pump_name is not None:
        new_name = req.pump_name.strip()
        if not new_name:
            raise HTTPException(400, "Pump name cannot be empty")
        # Check for duplicate active name
        existing = db.query(Pump).filter(
            Pump.pump_name == new_name,
            Pump.id != pump_id,
            (Pump.fleet_state == "In Service") | (Pump.fleet_state.is_(None))
        ).first()
        if existing:
            raise HTTPException(400, f"Pump name '{new_name}' is already used by another active pump")
        changes.append(f"name: {pump.pump_name} → {new_name}")
        pump.pump_name = new_name

    # Numeric fields
    numeric_fields = {
        "total_stages": req.total_stages,
        "packing_count": req.packing_count,
        "hole_1_count": req.hole_1_count,
        "hole_2_count": req.hole_2_count,
        "hole_3_count": req.hole_3_count,
        "hole_4_count": req.hole_4_count,
        "hole_5_count": req.hole_5_count,
    }
    for field, value in numeric_fields.items():
        if value is not None:
            if value < 0:
                raise HTTPException(400, f"{field} cannot be negative")
            old_ = getattr(pump, field)
            setattr(pump, field, value)
            changes.append(f"{field}: {old_} → {value}")

    if req.status is not None:
        valid = {"Active", "Standby", "Down", "Maintenance"}
        if req.status not in valid:
            raise HTTPException(400, f"Status must be one of: {valid}")
        changes.append(f"status: {pump.status} → {req.status}")
        pump.status = req.status

    if req.grease_type is not None:
        changes.append(f"grease: {pump.grease_type} → {req.grease_type}")
        pump.grease_type = req.grease_type

    if req.inspection_date is not None:
        try:
            d = dt_date.fromisoformat(req.inspection_date)
            changes.append(f"inspection: {pump.inspection_date} → {req.inspection_date}")
            pump.inspection_date = d
        except ValueError:
            raise HTTPException(400, "Invalid date. Use YYYY-MM-DD")

    if req.notes is not None:
        pump.notes = req.notes

    pump.last_updated = datetime.utcnow()
    after = json.dumps(pump.snapshot())

    action_type = "Rename Pump" if req.pump_name is not None and len(changes) == 1 else "Manual Edit"
    _log_action(db, req.operator_name, pump, action_type, before, after,
                req.comment or "; ".join(changes))
    db.commit()
    return pump.to_dict()


# ══════════════════════════════════════════════════════════════════════
# RESET COUNTERS / FULL RESET
# ══════════════════════════════════════════════════════════════════════

@router.post("/pumps/{pump_id}/reset-counters")
def reset_counters(pump_id: int, req: ResetCountersRequest, db: Session = Depends(get_db)):
    pump = db.query(Pump).filter(Pump.id == pump_id).first()
    if not pump:
        raise HTTPException(404, "Pump not found")
    before = json.dumps(pump.snapshot())
    if req.reset_packing:
        pump.packing_count = 0
    if req.reset_holes:
        pump.hole_1_count = 0
        pump.hole_2_count = 0
        pump.hole_3_count = 0
        pump.hole_4_count = 0
        pump.hole_5_count = 0
    if req.reset_stages:
        pump.total_stages = 0
    pump.last_updated = datetime.utcnow()
    after = json.dumps(pump.snapshot())
    _log_action(db, req.operator_name, pump, "Reset Counters", before, after, req.comment)
    db.commit()
    return pump.to_dict()


@router.post("/pumps/{pump_id}/full-reset")
def full_reset(pump_id: int, req: FullResetRequest, db: Session = Depends(get_db)):
    pump = db.query(Pump).filter(Pump.id == pump_id).first()
    if not pump:
        raise HTTPException(404, "Pump not found")
    before = json.dumps(pump.snapshot())
    pump.total_stages = 0
    pump.packing_count = 0
    pump.hole_1_count = 0
    pump.hole_2_count = 0
    pump.hole_3_count = 0
    pump.hole_4_count = 0
    pump.hole_5_count = 0
    pump.last_updated = datetime.utcnow()
    after = json.dumps(pump.snapshot())
    _log_action(db, req.operator_name, pump, "Full Reset", before, after, req.comment)
    db.commit()
    return pump.to_dict()


# ══════════════════════════════════════════════════════════════════════
# UPDATE LIMITS
# ══════════════════════════════════════════════════════════════════════

@router.post("/pumps/{pump_id}/update-limits")
def update_limits(pump_id: int, req: UpdateLimitsRequest, db: Session = Depends(get_db)):
    pump = db.query(Pump).filter(Pump.id == pump_id).first()
    if not pump:
        raise HTTPException(404, "Pump not found")
    before = json.dumps(pump.snapshot())
    changes = []
    limit_fields = [
        ("packing_warn", req.packing_warn),
        ("packing_crit", req.packing_crit),
        ("hole_warn", req.hole_warn),
        ("hole_limit", req.hole_limit),
        ("sv_warn", req.sv_warn),
        ("sv_limit", req.sv_limit),
        ("stage_warn", req.stage_warn),
        ("stage_crit", req.stage_crit),
    ]
    for field_name, value in limit_fields:
        if value is not None:
            old = getattr(pump, field_name)
            setattr(pump, field_name, value)
            changes.append(f"{field_name}: {old} → {value}")
    pump.last_updated = datetime.utcnow()
    after = json.dumps(pump.snapshot())
    _log_action(db, req.operator_name, pump, "Update Limits", before, after,
                req.comment or "; ".join(changes))
    db.commit()
    return pump.to_dict()


# ══════════════════════════════════════════════════════════════════════
# REMOVE PUMP / RESTORE PUMP
# ══════════════════════════════════════════════════════════════════════

@router.post("/pumps/{pump_id}/remove")
def remove_pump(pump_id: int, req: RemovePumpRequest, db: Session = Depends(get_db)):
    """Move pump to Removed state — keeps all data, just hides from main dashboard."""
    pump = db.query(Pump).filter(Pump.id == pump_id).first()
    if not pump:
        raise HTTPException(404, "Pump not found")
    if pump.fleet_state == "Removed":
        raise HTTPException(400, "Pump is already removed")

    before = json.dumps(pump.snapshot())
    pump.fleet_state = "Removed"
    pump.removed_date = datetime.utcnow()
    pump.removed_by = req.operator_name
    pump.removal_reason = req.reason or ""
    pump.last_updated = datetime.utcnow()
    after = json.dumps(pump.snapshot())

    _log_action(db, req.operator_name, pump, "Remove Pump", before, after,
                req.comment or req.reason or "")
    db.commit()
    return {"message": f"{pump.pump_name} removed from fleet", "pump": pump.to_dict()}


@router.post("/pumps/{pump_id}/restore")
def restore_pump(pump_id: int, req: RestorePumpRequest, db: Session = Depends(get_db)):
    """Restore a removed pump back to In Service."""
    pump = db.query(Pump).filter(Pump.id == pump_id).first()
    if not pump:
        raise HTTPException(404, "Pump not found")
    if pump.fleet_state != "Removed":
        raise HTTPException(400, "Pump is not in Removed state")

    before = json.dumps(pump.snapshot())
    pump.fleet_state = "In Service"
    pump.removed_date = None
    pump.removed_by = ""
    pump.removal_reason = ""
    pump.last_updated = datetime.utcnow()
    after = json.dumps(pump.snapshot())

    _log_action(db, req.operator_name, pump, "Restore Pump", before, after, req.comment)
    db.commit()
    return {"message": f"{pump.pump_name} restored to fleet", "pump": pump.to_dict()}


# ══════════════════════════════════════════════════════════════════════
# UNIVERSAL UNDO — restores from before_value snapshot
# ══════════════════════════════════════════════════════════════════════

@router.post("/undo")
def undo_last_action(req: UndoRequest, db: Session = Depends(get_db)):
    """Undo the last single-pump action by restoring the before_value snapshot."""
    last_log = (
        db.query(HistoryLog)
        .filter(HistoryLog.pump_id.isnot(None))
        .filter(HistoryLog.action_type != "Undo")
        .filter(HistoryLog.undone == False)
        .filter(HistoryLog.before_value != "")
        .order_by(HistoryLog.timestamp.desc())
        .first()
    )
    if not last_log:
        raise HTTPException(400, "Nothing to undo")

    pump = db.query(Pump).filter(Pump.id == last_log.pump_id).first()
    if not pump:
        raise HTTPException(400, "Pump no longer exists")

    try:
        snapshot = json.loads(last_log.before_value)
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(400, "Cannot undo — history data is not in snapshot format")

    # Capture current state before undo
    current_snapshot = json.dumps(pump.snapshot())

    # Restore all fields from snapshot
    restorable_numeric = [
        "total_stages", "packing_count",
        "hole_1_count", "hole_2_count", "hole_3_count",
        "hole_4_count", "hole_5_count",
    ]
    for field in restorable_numeric:
        if field in snapshot:
            setattr(pump, field, snapshot[field])

    if "pump_name" in snapshot:
        pump.pump_name = snapshot["pump_name"]
    if "status" in snapshot:
        pump.status = snapshot["status"]
    if "grease_type" in snapshot:
        pump.grease_type = snapshot["grease_type"]
    if "notes" in snapshot:
        pump.notes = snapshot["notes"]
    if "inspection_date" in snapshot:
        if snapshot["inspection_date"]:
            try:
                pump.inspection_date = dt_date.fromisoformat(snapshot["inspection_date"])
            except ValueError:
                pass
        else:
            pump.inspection_date = None

    # Restore fleet state (handles undo of remove/restore)
    if "fleet_state" in snapshot:
        pump.fleet_state = snapshot["fleet_state"]
        if snapshot["fleet_state"] == "Removed":
            # Undoing a restore — re-mark as removed
            pass  # removed_date/by/reason won't be perfect but state is correct
        elif snapshot["fleet_state"] == "In Service":
            pump.removed_date = None
            pump.removed_by = ""
            pump.removal_reason = ""

    # Restore limit fields
    limit_fields = ["packing_warn", "packing_crit", "hole_warn", "hole_limit", "sv_warn", "sv_limit"]
    for field in limit_fields:
        if field in snapshot:
            setattr(pump, field, snapshot[field])

    # Restore group_name
    if "group_name" in snapshot:
        pump.group_name = snapshot["group_name"]

    pump.last_updated = datetime.utcnow()

    # Mark the original action as undone
    last_log.undone = True

    # Log the undo itself
    _log_action(db, req.operator_name, pump, "Undo",
                current_snapshot, json.dumps(pump.snapshot()),
                f"Undid: {last_log.action_type}")
    db.commit()

    return {
        "message": f"Undid '{last_log.action_type}' on {pump.pump_name}",
        "pump": pump.to_dict(),
        "undone_action": last_log.action_type,
    }


# ── History Log ─────────────────────────────────────────────────────

@router.get("/history")
def get_history(
    limit: int = Query(100, ge=1, le=1000),
    pump_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    q = db.query(HistoryLog).order_by(HistoryLog.timestamp.desc())
    if pump_id:
        q = q.filter(HistoryLog.pump_id == pump_id)
    logs = q.limit(limit).all()
    return [l.to_dict() for l in logs]


# ── Well Info ───────────────────────────────────────────────────────

@router.get("/well-info")
def get_well_info(db: Session = Depends(get_db)):
    well = db.query(WellInfo).first()
    if not well:
        well = WellInfo(well_name="HZEM-4408", pad_name="", location="", rig_name="")
        db.add(well)
        db.commit()
    return well.to_dict()


@router.post("/well-info")
def update_well_info(req: WellInfoUpdate, db: Session = Depends(get_db)):
    well = db.query(WellInfo).first()
    if not well:
        well = WellInfo()
        db.add(well)

    if req.well_name is not None:
        well.well_name = req.well_name
    if req.location is not None:
        well.location = req.location
    if req.rig_name is not None:
        well.rig_name = req.rig_name
    if req.pad_name is not None:
        well.pad_name = req.pad_name
    if req.max_stages_vs is not None:
        well.max_stages_vs = req.max_stages_vs

    db.commit()
    return well.to_dict()


# ── Excel Import/Export ─────────────────────────────────────────────

@router.post("/import-excel")
async def import_excel(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Please upload an .xlsx or .xlsm file")
    contents = await file.read()
    result = import_from_excel(contents, db)
    return {"message": "Import complete", **result}


@router.get("/export-excel")
def export_excel(db: Session = Depends(get_db)):
    try:
        data = export_to_excel(db)
        if not data:
            raise HTTPException(500, "Generated Excel file is empty")
        logger.info(f"Excel export: {len(data)} bytes")
        return StreamingResponse(
            BytesIO(data),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=maintenance_tracker.xlsx"}
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Excel export failed")
        raise HTTPException(500, f"Excel export error: {str(e)}")


# ── PDF Report ──────────────────────────────────────────────────────

@router.get("/export-pdf")
def export_pdf(operator: str = Query("N/A"), db: Session = Depends(get_db)):
    try:
        data = generate_pdf_report(db, operator)
        if not data:
            raise HTTPException(500, "Generated PDF file is empty")
        logger.info(f"PDF export: {len(data)} bytes, operator={operator}")
        return StreamingResponse(
            BytesIO(data),
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=maintenance_report.pdf"}
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("PDF export failed")
        raise HTTPException(500, f"PDF export error: {str(e)}")


# ══════════════════════════════════════════════════════════════════════
# SNAPSHOT SAVE / LOAD
# ══════════════════════════════════════════════════════════════════════

SNAPSHOT_VERSION = "2.5"


@router.get("/snapshot/save")
def save_snapshot(operator: str = Query("System"), db: Session = Depends(get_db)):
    """Export the ENTIRE system state as a JSON snapshot file."""
    try:
        # ── All pumps (in-service + removed)
        all_pumps = db.query(Pump).all()
        pumps_data = []
        for p in all_pumps:
            pumps_data.append({
                "station": p.station,
                "pump_name": p.pump_name,
                "trailer": p.trailer or "",
                "model": p.model or "",
                "status": p.status,
                "grease_type": p.grease_type,
                "total_stages": p.total_stages,
                "packing_count": p.packing_count,
                "hole_1_count": p.hole_1_count,
                "hole_2_count": p.hole_2_count,
                "hole_3_count": p.hole_3_count,
                "hole_4_count": p.hole_4_count,
                "hole_5_count": p.hole_5_count,
                "inspection_date": p.inspection_date.isoformat() if p.inspection_date else None,
                "notes": p.notes or "",
                "sort_order": p.sort_order or p.station or 0,
                "packing_warn": p.packing_warn,
                "packing_crit": p.packing_crit,
                "hole_warn": p.hole_warn,
                "hole_limit": p.hole_limit,
                "sv_warn": p.sv_warn,
                "sv_limit": p.sv_limit,
                "stage_warn": p.stage_warn or 200,
                "stage_crit": p.stage_crit or 300,
                "color_stages": p.color_stages,
                "color_hole_1": p.color_hole_1,
                "color_hole_2": p.color_hole_2,
                "color_hole_3": p.color_hole_3,
                "color_hole_4": p.color_hole_4,
                "color_hole_5": p.color_hole_5,
                "fleet_state": p.fleet_state or "In Service",
                "removed_date": p.removed_date.isoformat() if p.removed_date else None,
                "removed_by": p.removed_by or "",
                "removal_reason": p.removal_reason or "",
                "group_name": p.group_name or "Unassigned",
                "last_updated": p.last_updated.isoformat() if p.last_updated else None,
            })

        # ── History log
        all_history = db.query(HistoryLog).order_by(HistoryLog.timestamp.asc()).all()
        history_data = [h.to_dict() for h in all_history]

        # ── Well info
        well = db.query(WellInfo).first()
        well_data = well.to_dict() if well else {}

        snapshot = {
            "_meta": {
                "version": SNAPSHOT_VERSION,
                "app": "Amri Maintenance Tracker",
                "saved_at": datetime.utcnow().isoformat(),
                "saved_by": operator,
                "pump_count": len(pumps_data),
                "history_count": len(history_data),
            },
            "pumps": pumps_data,
            "history": history_data,
            "well_info": well_data,
        }

        # Log the save action
        log = HistoryLog(
            operator_name=operator,
            pump_id=None,
            pump_name="",
            action_type="Save Snapshot",
            before_value="",
            after_value=json.dumps({"pump_count": len(pumps_data), "history_count": len(history_data)}),
            comment=f"Full system snapshot saved by {operator}",
        )
        db.add(log)
        db.commit()

        content = json.dumps(snapshot, indent=2, ensure_ascii=False)
        timestamp_str = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"amri_snapshot_{timestamp_str}.json"

        return StreamingResponse(
            BytesIO(content.encode("utf-8")),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        logger.exception("Snapshot save failed")
        raise HTTPException(500, f"Snapshot save error: {str(e)}")


@router.post("/snapshot/load")
async def load_snapshot(
    file: UploadFile = File(...),
    operator: str = Query("System"),
    db: Session = Depends(get_db),
):
    """Restore full system state from a JSON snapshot file."""
    if not file.filename.endswith(".json"):
        raise HTTPException(400, "Snapshot file must be .json")

    try:
        raw = await file.read()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise HTTPException(400, "Invalid JSON file — cannot parse")

        # ── Validate structure
        required_keys = {"_meta", "pumps", "history", "well_info"}
        missing = required_keys - set(data.keys())
        if missing:
            raise HTTPException(400, f"Invalid snapshot: missing keys {missing}")

        meta = data.get("_meta", {})
        if meta.get("app") != "Amri Maintenance Tracker":
            raise HTTPException(400, "This file is not an Amri Maintenance Tracker snapshot")

        pumps_data = data["pumps"]
        history_data = data["history"]
        well_data = data["well_info"]

        if not isinstance(pumps_data, list):
            raise HTTPException(400, "Invalid snapshot: 'pumps' must be an array")
        if not isinstance(history_data, list):
            raise HTTPException(400, "Invalid snapshot: 'history' must be an array")

        # Validate each pump has required fields
        pump_required = {"pump_name", "station"}
        for i, p in enumerate(pumps_data):
            pm = pump_required - set(p.keys())
            if pm:
                raise HTTPException(400, f"Pump #{i+1} missing fields: {pm}")

        # ── Count current data for before-log
        old_pump_count = db.query(Pump).count()
        old_history_count = db.query(HistoryLog).count()

        # ── Wipe existing data
        db.query(HistoryLog).delete()
        db.query(Pump).delete()
        db.query(WellInfo).delete()
        db.flush()

        # ── Restore pumps
        for p in pumps_data:
            insp_date = None
            if p.get("inspection_date"):
                try:
                    insp_date = dt_date.fromisoformat(p["inspection_date"])
                except (ValueError, TypeError):
                    insp_date = None

            removed_dt = None
            if p.get("removed_date"):
                try:
                    removed_dt = datetime.fromisoformat(p["removed_date"])
                except (ValueError, TypeError):
                    removed_dt = None

            last_upd = None
            if p.get("last_updated"):
                try:
                    last_upd = datetime.fromisoformat(p["last_updated"])
                except (ValueError, TypeError):
                    last_upd = datetime.utcnow()

            pump = Pump(
                station=p.get("station", 0),
                sort_order=p.get("sort_order", p.get("station", 0)),
                pump_name=p["pump_name"],
                trailer=p.get("trailer", ""),
                model=p.get("model", ""),
                status=p.get("status", "Active"),
                grease_type=p.get("grease_type", "Oil"),
                total_stages=p.get("total_stages", 0),
                packing_count=p.get("packing_count", 0),
                hole_1_count=p.get("hole_1_count", 0),
                hole_2_count=p.get("hole_2_count", 0),
                hole_3_count=p.get("hole_3_count", 0),
                hole_4_count=p.get("hole_4_count", 0),
                hole_5_count=p.get("hole_5_count", 0),
                inspection_date=insp_date,
                notes=p.get("notes", ""),
                packing_warn=p.get("packing_warn", 140),
                packing_crit=p.get("packing_crit", 160),
                hole_warn=p.get("hole_warn", 35),
                hole_limit=p.get("hole_limit", 45),
                sv_warn=p.get("sv_warn", 35),
                sv_limit=p.get("sv_limit", 45),
                stage_warn=p.get("stage_warn", 200),
                stage_crit=p.get("stage_crit", 300),
                color_stages=p.get("color_stages"),
                color_hole_1=p.get("color_hole_1"),
                color_hole_2=p.get("color_hole_2"),
                color_hole_3=p.get("color_hole_3"),
                color_hole_4=p.get("color_hole_4"),
                color_hole_5=p.get("color_hole_5"),
                fleet_state=p.get("fleet_state", "In Service"),
                removed_date=removed_dt,
                removed_by=p.get("removed_by", ""),
                removal_reason=p.get("removal_reason", ""),
                group_name=p.get("group_name", "Unassigned"),
                last_updated=last_upd or datetime.utcnow(),
            )
            db.add(pump)

        # ── Restore history
        for h in history_data:
            ts = None
            if h.get("timestamp"):
                try:
                    ts = datetime.fromisoformat(h["timestamp"])
                except (ValueError, TypeError):
                    ts = datetime.utcnow()

            log = HistoryLog(
                timestamp=ts or datetime.utcnow(),
                operator_name=h.get("operator_name", "Unknown"),
                pump_id=None,  # foreign key will be invalid; store as null
                pump_name=h.get("pump_name", ""),
                action_type=h.get("action_type", "Unknown"),
                before_value=h.get("before_value", ""),
                after_value=h.get("after_value", ""),
                comment=h.get("comment", ""),
                undone=h.get("undone", False),
            )
            db.add(log)

        # ── Restore well info
        if well_data:
            well = WellInfo(
                well_name=well_data.get("well_name", ""),
                location=well_data.get("location", ""),
                rig_name=well_data.get("rig_name", ""),
                pad_name=well_data.get("pad_name", ""),
                max_stages_vs=well_data.get("max_stages_vs", 40),
            )
            db.add(well)

        # ── Log the load action (after restore so it appears in current history)
        load_log = HistoryLog(
            operator_name=operator,
            pump_id=None,
            pump_name="",
            action_type="Load Snapshot",
            before_value=json.dumps({"old_pumps": old_pump_count, "old_history": old_history_count}),
            after_value=json.dumps({"new_pumps": len(pumps_data), "new_history": len(history_data)}),
            comment=f"Snapshot loaded from '{file.filename}' by {operator} (v{meta.get('version', '?')})",
        )
        db.add(load_log)
        db.commit()

        return {
            "message": f"Snapshot loaded: {len(pumps_data)} pumps, {len(history_data)} history records restored",
            "pumps_restored": len(pumps_data),
            "history_restored": len(history_data),
            "snapshot_version": meta.get("version"),
            "original_save_date": meta.get("saved_at"),
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception("Snapshot load failed")
        raise HTTPException(500, f"Snapshot load error: {str(e)}")


# ══════════════════════════════════════════════════════════════════════
# REPLACE PUMP
# ══════════════════════════════════════════════════════════════════════

@router.post("/pumps/{pump_id}/replace")
def replace_pump(pump_id: int, req: ReplacePumpRequest, db: Session = Depends(get_db)):
    """Replace an existing pump: old pump → Removed, new pump takes its place."""
    old_pump = db.query(Pump).filter(Pump.id == pump_id).first()
    if not old_pump:
        raise HTTPException(404, "Pump not found")
    if old_pump.fleet_state == "Removed":
        raise HTTPException(400, "Cannot replace an already-removed pump")

    new_name = req.new_pump_name.strip()
    if not new_name:
        raise HTTPException(400, "New pump name is required")

    existing = db.query(Pump).filter(
        Pump.pump_name == new_name,
        (Pump.fleet_state == "In Service") | (Pump.fleet_state.is_(None))
    ).first()
    if existing:
        raise HTTPException(400, f"Pump name '{new_name}' already exists")

    # Snapshot old pump before removal
    old_before = json.dumps(old_pump.snapshot())

    # Remove old pump
    old_pump.fleet_state = "Removed"
    old_pump.removed_date = datetime.utcnow()
    old_pump.removed_by = req.operator_name
    old_pump.removal_reason = req.reason or f"Replaced by {new_name}"

    # Parse inspection date
    insp_date = None
    if req.inspection_date:
        try:
            insp_date = dt_date.fromisoformat(req.inspection_date)
        except ValueError:
            raise HTTPException(400, "Invalid inspection date")

    # Create new pump at same station/sort_order
    new_pump = Pump(
        pump_name=new_name,
        station=old_pump.station,
        sort_order=old_pump.sort_order or old_pump.station,
        model=req.model,
        status=req.status,
        grease_type=req.grease_type,
        total_stages=req.total_stages,
        hole_1_count=req.hole_1_count,
        hole_2_count=req.hole_2_count,
        hole_3_count=req.hole_3_count,
        hole_4_count=req.hole_4_count,
        hole_5_count=req.hole_5_count,
        inspection_date=insp_date,
        notes=req.notes,
        fleet_state="In Service",
        group_name=req.group_name or old_pump.group_name or "Unassigned",
        hole_warn=old_pump.hole_warn,
        hole_limit=old_pump.hole_limit,
        sv_warn=old_pump.sv_warn,
        sv_limit=old_pump.sv_limit,
        stage_warn=old_pump.stage_warn,
        stage_crit=old_pump.stage_crit,
        replaced_pump_id=old_pump.id,
        last_updated=datetime.utcnow(),
    )
    db.add(new_pump)
    db.flush()

    old_pump.replaced_by_id = new_pump.id

    # Log removal of old pump
    _log_action(db, req.operator_name, old_pump, "Replace Pump (Removed)",
                old_before, json.dumps(old_pump.snapshot()),
                f"Replaced by {new_name}")

    # Log creation of new pump
    _log_action(db, req.operator_name, new_pump, "Replace Pump (New)",
                "", json.dumps(new_pump.snapshot()),
                req.comment or f"Replaces {old_pump.pump_name}")

    db.commit()
    return {"message": f"{old_pump.pump_name} replaced by {new_name}", "new_pump": new_pump.to_dict()}


# ══════════════════════════════════════════════════════════════════════
# MOVE UP / DOWN (reorder)
# ══════════════════════════════════════════════════════════════════════


def _normalize_sort_order(db: Session):
    """
    Assign sequential sort_order 1..N to all in-service
    pumps.  Returns the ordered list so callers can index
    by position.  This guarantees every pump has a unique,
    gap-free position before any swap.
    """
    pumps = (
        db.query(Pump)
        .filter(
            (Pump.fleet_state == "In Service")
            | (Pump.fleet_state.is_(None))
        )
        .order_by(Pump.sort_order, Pump.station)
        .all()
    )
    for pos, p in enumerate(pumps, start=1):
        if p.sort_order != pos:
            p.sort_order = pos
    db.flush()          # write sequential values before swap
    return pumps

@router.post("/pumps/{pump_id}/move-up")
def move_pump_up(pump_id: int, req: OperatorAction,
                 db: Session = Depends(get_db)):
    """Move a pump one position up (swap with the pump above)."""
    ordered = _normalize_sort_order(db)

    idx = next((i for i, p in enumerate(ordered)
                if p.id == pump_id), None)
    if idx is None:
        raise HTTPException(404, "Pump not found")
    if idx == 0:
        raise HTTPException(400, "Already at the top")

    pump_a = ordered[idx]      # the one moving up
    pump_b = ordered[idx - 1]  # the one moving down

    before_a = json.dumps(pump_a.snapshot())

    # Swap only sort_order — station stays unchanged
    pump_a.sort_order, pump_b.sort_order = (
        pump_b.sort_order, pump_a.sort_order
    )
    pump_a.last_updated = datetime.utcnow()
    pump_b.last_updated = datetime.utcnow()

    after_a = json.dumps(pump_a.snapshot())
    _log_action(
        db, req.operator_name, pump_a, "Move Up",
        before_a, after_a,
        f"{pump_a.pump_name} moved from position "
        f"{idx + 1} to {idx}, swapped with "
        f"{pump_b.pump_name}",
    )
    db.commit()
    return {"message": f"{pump_a.pump_name} moved up"}


@router.post("/pumps/{pump_id}/move-down")
def move_pump_down(pump_id: int, req: OperatorAction,
                   db: Session = Depends(get_db)):
    """Move a pump one position down (swap with the pump below)."""
    ordered = _normalize_sort_order(db)

    idx = next((i for i, p in enumerate(ordered)
                if p.id == pump_id), None)
    if idx is None:
        raise HTTPException(404, "Pump not found")
    if idx >= len(ordered) - 1:
        raise HTTPException(400, "Already at the bottom")

    pump_a = ordered[idx]      # the one moving down
    pump_b = ordered[idx + 1]  # the one moving up

    before_a = json.dumps(pump_a.snapshot())

    pump_a.sort_order, pump_b.sort_order = (
        pump_b.sort_order, pump_a.sort_order
    )
    pump_a.last_updated = datetime.utcnow()
    pump_b.last_updated = datetime.utcnow()

    after_a = json.dumps(pump_a.snapshot())
    _log_action(
        db, req.operator_name, pump_a, "Move Down",
        before_a, after_a,
        f"{pump_a.pump_name} moved from position "
        f"{idx + 1} to {idx + 2}, swapped with "
        f"{pump_b.pump_name}",
    )
    db.commit()
    return {"message": f"{pump_a.pump_name} moved down"}


# ══════════════════════════════════════════════════════════════════════
# SET COLOR OVERRIDE
# ══════════════════════════════════════════════════════════════════════

@router.post("/pumps/{pump_id}/set-color")
def set_color_override(pump_id: int, req: SetColorRequest, db: Session = Depends(get_db)):
    """Set or clear a manual color override on a specific cell."""
    pump = db.query(Pump).filter(Pump.id == pump_id).first()
    if not pump:
        raise HTTPException(404, "Pump not found")

    valid_fields = {"stages", "hole_1", "hole_2", "hole_3", "hole_4", "hole_5"}
    if req.field not in valid_fields:
        raise HTTPException(400, f"Field must be one of: {valid_fields}")

    valid_colors = {"green", "yellow", "red", None}
    if req.color not in valid_colors:
        raise HTTPException(400, "Color must be green, yellow, red, or null")

    before = json.dumps(pump.snapshot())
    col_name = f"color_{req.field}"
    old_color = getattr(pump, col_name, None)
    setattr(pump, col_name, req.color)
    pump.last_updated = datetime.utcnow()
    after = json.dumps(pump.snapshot())

    action = "Clear Color Override" if req.color is None else "Set Color Override"
    _log_action(db, req.operator_name, pump, action,
                before, after,
                req.comment or f"{req.field}: {old_color or 'auto'} → {req.color or 'auto'}")
    db.commit()
    return {"message": f"Color updated on {pump.pump_name}.{req.field}", "pump": pump.to_dict()}
