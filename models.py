"""
SQLAlchemy ORM models for the Maintenance Tracker.
v3.1 — Removed packing as active field, added color overrides, sort_order, replacement tracking.
"""
from datetime import datetime, date
from sqlalchemy import (
    Column, Integer, String, DateTime, Date, Text, Float, ForeignKey, Boolean
)
from sqlalchemy.orm import relationship
from database import Base


class GlobalSettings(Base):
    """Singleton row holding system-wide default limits and thresholds."""
    __tablename__ = "global_settings"

    id = Column(Integer, primary_key=True, index=True)
    # Packing fields kept for backward compat but not actively used
    packing_warn_default = Column(Integer, default=140)
    packing_crit_default = Column(Integer, default=160)
    hole_warn_default = Column(Integer, default=35)
    hole_limit_default = Column(Integer, default=45)
    sv_warn_default = Column(Integer, default=35)
    sv_limit_default = Column(Integer, default=45)
    # Stage thresholds (new)
    stage_warn_default = Column(Integer, default=200)
    stage_crit_default = Column(Integer, default=300)
    last_updated = Column(DateTime, default=datetime.utcnow)
    updated_by = Column(String(100), default="")

    def to_dict(self):
        return {
            "packing_warn_default": self.packing_warn_default,
            "packing_crit_default": self.packing_crit_default,
            "hole_warn_default": self.hole_warn_default,
            "hole_limit_default": self.hole_limit_default,
            "sv_warn_default": self.sv_warn_default,
            "sv_limit_default": self.sv_limit_default,
            "stage_warn_default": self.stage_warn_default or 200,
            "stage_crit_default": self.stage_crit_default or 300,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
            "updated_by": self.updated_by or "",
        }


class Pump(Base):
    __tablename__ = "pumps"

    id = Column(Integer, primary_key=True, index=True)
    station = Column(Integer, nullable=False)           # Station number (1-24)
    sort_order = Column(Integer, default=0)              # Manual sort order
    pump_name = Column(String(50), nullable=False)       # e.g. HP-25
    trailer = Column(String(50), default="")             # e.g. TH-55
    model = Column(String(50), default="")               # e.g. GD-4"
    status = Column(String(20), default="Active")        # Active / Standby / Down / Maintenance
    grease_type = Column(String(20), default="Oil")      # Oil / Grease
    total_stages = Column(Integer, default=0)
    packing_count = Column(Integer, default=0)           # Kept for backward compat
    hole_1_count = Column(Integer, default=0)
    hole_2_count = Column(Integer, default=0)
    hole_3_count = Column(Integer, default=0)
    hole_4_count = Column(Integer, default=0)
    hole_5_count = Column(Integer, default=0)
    inspection_date = Column(Date, nullable=True)
    notes = Column(Text, default="")
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ── Per-pump configurable limits ────────────────────────
    packing_warn = Column(Integer, default=140)
    packing_crit = Column(Integer, default=160)
    hole_warn = Column(Integer, default=35)
    hole_limit = Column(Integer, default=45)
    sv_warn = Column(Integer, default=35)
    sv_limit = Column(Integer, default=45)
    stage_warn = Column(Integer, default=200)
    stage_crit = Column(Integer, default=300)

    # ── Color overrides (manual) ────────────────────────────
    # null = auto (use thresholds), "green"/"yellow"/"red" = manual override
    color_stages = Column(String(10), nullable=True, default=None)
    color_hole_1 = Column(String(10), nullable=True, default=None)
    color_hole_2 = Column(String(10), nullable=True, default=None)
    color_hole_3 = Column(String(10), nullable=True, default=None)
    color_hole_4 = Column(String(10), nullable=True, default=None)
    color_hole_5 = Column(String(10), nullable=True, default=None)

    # ── Fleet lifecycle ─────────────────────────────────────
    fleet_state = Column(String(20), default="In Service")  # In Service / Removed
    removed_date = Column(DateTime, nullable=True)
    removed_by = Column(String(100), default="")
    removal_reason = Column(Text, default="")

    # ── Replacement tracking ────────────────────────────────
    replaced_by_id = Column(Integer, nullable=True)     # ID of the replacement pump
    replaced_pump_id = Column(Integer, nullable=True)   # ID of the pump this replaced

    # ── Group / Load Distribution ──────────────────────────
    group_name = Column(String(30), default="Unassigned")

    history = relationship("HistoryLog", back_populates="pump", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "station": self.station,
            "sort_order": self.sort_order or self.station or 0,
            "pump_name": self.pump_name,
            "trailer": self.trailer,
            "model": self.model,
            "status": self.status,
            "grease_type": self.grease_type,
            "total_stages": self.total_stages,
            "packing_count": self.packing_count,
            "hole_1_count": self.hole_1_count,
            "hole_2_count": self.hole_2_count,
            "hole_3_count": self.hole_3_count,
            "hole_4_count": self.hole_4_count,
            "hole_5_count": self.hole_5_count,
            "inspection_date": self.inspection_date.isoformat() if self.inspection_date else None,
            "notes": self.notes,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
            "alerts": self._get_alerts(),
            "limits": {
                "packing_warn": self.packing_warn,
                "packing_crit": self.packing_crit,
                "hole_warn": self.hole_warn,
                "hole_limit": self.hole_limit,
                "sv_warn": self.sv_warn,
                "sv_limit": self.sv_limit,
                "stage_warn": self.stage_warn or 200,
                "stage_crit": self.stage_crit or 300,
            },
            "color_overrides": {
                "stages": self.color_stages,
                "hole_1": self.color_hole_1,
                "hole_2": self.color_hole_2,
                "hole_3": self.color_hole_3,
                "hole_4": self.color_hole_4,
                "hole_5": self.color_hole_5,
            },
            "fleet_state": self.fleet_state or "In Service",
            "removed_date": self.removed_date.isoformat() if self.removed_date else None,
            "removed_by": self.removed_by or "",
            "removal_reason": self.removal_reason or "",
            "replaced_by_id": self.replaced_by_id,
            "replaced_pump_id": self.replaced_pump_id,
            "group_name": self.group_name or "Unassigned",
        }

    def _get_alerts(self):
        alerts = {}
        # Stage alerts
        sw = self.stage_warn or 200
        sc = self.stage_crit or 300
        if self.color_stages:
            alerts["stages"] = self.color_stages
        elif self.total_stages >= sc:
            alerts["stages"] = "red"
        elif self.total_stages >= sw:
            alerts["stages"] = "yellow"
        else:
            alerts["stages"] = "green"

        # Hole alerts (individual)
        hw = self.hole_warn or 35
        hl = self.hole_limit or 45
        for i in range(1, 6):
            color_override = getattr(self, f"color_hole_{i}", None)
            val = getattr(self, f"hole_{i}_count", 0)
            if color_override:
                alerts[f"hole_{i}"] = color_override
            elif val >= hl:
                alerts[f"hole_{i}"] = "red"
            elif val >= hw:
                alerts[f"hole_{i}"] = "yellow"
            else:
                alerts[f"hole_{i}"] = "green"

        # Overall seat_valve alert (max of all holes)
        max_hole = max(
            self.hole_1_count, self.hole_2_count, self.hole_3_count,
            self.hole_4_count, self.hole_5_count
        )
        if max_hole >= hl:
            alerts["seat_valve"] = "red"
        elif max_hole >= hw:
            alerts["seat_valve"] = "yellow"
        else:
            alerts["seat_valve"] = "green"

        return alerts

    def snapshot(self):
        """Full snapshot of all mutable fields for undo support."""
        return {
            "pump_name": self.pump_name,
            "total_stages": self.total_stages,
            "packing_count": self.packing_count,
            "hole_1_count": self.hole_1_count,
            "hole_2_count": self.hole_2_count,
            "hole_3_count": self.hole_3_count,
            "hole_4_count": self.hole_4_count,
            "hole_5_count": self.hole_5_count,
            "status": self.status,
            "grease_type": self.grease_type,
            "inspection_date": self.inspection_date.isoformat() if self.inspection_date else None,
            "notes": self.notes,
            "fleet_state": self.fleet_state or "In Service",
            "group_name": self.group_name or "Unassigned",
            "sort_order": self.sort_order or 0,
            "station": self.station,
            "packing_warn": self.packing_warn,
            "packing_crit": self.packing_crit,
            "hole_warn": self.hole_warn,
            "hole_limit": self.hole_limit,
            "sv_warn": self.sv_warn,
            "sv_limit": self.sv_limit,
            "stage_warn": self.stage_warn or 200,
            "stage_crit": self.stage_crit or 300,
            "color_stages": self.color_stages,
            "color_hole_1": self.color_hole_1,
            "color_hole_2": self.color_hole_2,
            "color_hole_3": self.color_hole_3,
            "color_hole_4": self.color_hole_4,
            "color_hole_5": self.color_hole_5,
        }


class HistoryLog(Base):
    __tablename__ = "history_log"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    operator_name = Column(String(100), nullable=False)
    pump_id = Column(Integer, ForeignKey("pumps.id"), nullable=True)
    pump_name = Column(String(50), default="")
    action_type = Column(String(50), nullable=False)
    before_value = Column(Text, default="")
    after_value = Column(Text, default="")
    comment = Column(Text, default="")
    undone = Column(Boolean, default=False)

    pump = relationship("Pump", back_populates="history")

    def to_dict(self):
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "operator_name": self.operator_name,
            "pump_id": self.pump_id,
            "pump_name": self.pump_name,
            "action_type": self.action_type,
            "before_value": self.before_value,
            "after_value": self.after_value,
            "comment": self.comment,
            "undone": self.undone or False,
        }


class WellInfo(Base):
    __tablename__ = "well_info"

    id = Column(Integer, primary_key=True, index=True)
    well_name = Column(String(200), default="")
    location = Column(String(200), default="")
    rig_name = Column(String(200), default="")
    pad_name = Column(String(200), default="")
    max_stages_vs = Column(Integer, default=40)

    def to_dict(self):
        return {
            "id": self.id,
            "well_name": self.well_name,
            "location": self.location,
            "rig_name": self.rig_name,
            "pad_name": self.pad_name,
            "max_stages_vs": self.max_stages_vs,
        }


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False)
    pin = Column(String(10), nullable=False)  # 4-digit password
    role = Column(String(20), default="operator")  # admin / operator
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {"id": self.id, "username": self.username, "role": self.role}
