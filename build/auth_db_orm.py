import json
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func
from database import (
    SessionLocal, User, Session, UsageDaily, KVStore, OrgApiKey, 
    GenerationEvent, School, VaultItem, VaultSave, init_db as db_init
)

SESSION_TTL_HOURS = 12
PBKDF2_ITERATIONS = 200_000

ROLES = ("super_admin", "admin", "professor")
QUOTA_TYPES = ("unlimited", "tokens", "count")

def _now_iso():
    return datetime.now(timezone.utc).isoformat()

def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS)
    return digest.hex(), salt

def verify_password(password, password_hash, salt):
    candidate, _ = hash_password(password, salt)
    return secrets.compare_digest(candidate, password_hash)

def init_db():
    db_init()
    with SessionLocal() as db:
        if db.query(User).count() == 0:
            username = "admin"
            password = secrets.token_urlsafe(9)
            pw_hash, salt = hash_password(password)
            user = User(
                username=username,
                password_hash=pw_hash,
                password_salt=salt,
                role="super_admin",
                created_at=_now_iso()
            )
            db.add(user)
            db.commit()
            return username, password
        return None

DEFAULT_PERMISSIONS = {
    "importZip": True, "importProgramme": True, "reset": True,
    "exportZip": True, "exportOlx": True, "modify": True,
}

def _json_list_or_none(raw):
    if raw is None: return None
    try: return json.loads(raw)
    except (TypeError, ValueError): return None

def _permissions_from_row(row_dict):
    raw = row_dict.get("permissions")
    merged = dict(DEFAULT_PERMISSIONS)
    if raw:
        try: merged.update(json.loads(raw))
        except (TypeError, ValueError): pass
    return merged

def _allowed_list(row_dict, col):
    raw = row_dict.get(col)
    if raw is None: return None
    try: return json.loads(raw)
    except (TypeError, ValueError): return None

def check_type_allowed(user_dict, ex_type):
    if user_dict["role"] != "professor": return True
    allowed = _allowed_list(user_dict, "allowed_types")
    return allowed is None or ex_type in allowed

def check_sujet_allowed(user_dict, sujet):
    if user_dict["role"] != "professor": return True
    allowed = _allowed_list(user_dict, "allowed_sujets")
    return allowed is None or sujet in allowed

def check_provider_allowed(user_dict, provider):
    if user_dict["role"] != "professor": return True
    allowed = _allowed_list(user_dict, "allowed_providers")
    return allowed is None or provider in allowed

def check_permission(user_dict, action):
    if user_dict["role"] != "professor": return True
    return _permissions_from_row(user_dict).get(action, True)

def _user_public(row_dict):
    if row_dict is None: return None
    return {
        "id": row_dict["id"],
        "username": row_dict["username"],
        "role": row_dict["role"],
        "schoolId": row_dict["school_id"],
        "niveauxAssignes": json.loads(row_dict["niveaux_assignes"]),
        "quotaDaily": row_dict["quota_daily"],
        "quotaWeekly": row_dict["quota_weekly"],
        "quotaMonthly": row_dict["quota_monthly"],
        "allowedTypes": _json_list_or_none(row_dict["allowed_types"]),
        "allowedSujets": _json_list_or_none(row_dict["allowed_sujets"]),
        "allowedProviders": _json_list_or_none(row_dict["allowed_providers"]),
        "canUseOwnKey": bool(row_dict["can_use_own_key"]),
        "permissions": _permissions_from_row(row_dict),
        "isActive": bool(row_dict["is_active"]),
        "createdAt": row_dict["created_at"],
    }

def resolve_school_id(user_dict, requested_school_id=None):
    if user_dict["role"] in ("admin", "professor"):
        return user_dict["school_id"]
    if requested_school_id:
        return int(requested_school_id)
    return None

def get_user_by_username(username):
    with SessionLocal() as db:
        user = db.query(User).filter(User.username == username).first()
        if user:
            # Need password fields for auth
            d = user.__dict__.copy()
            return d
        return None

def get_user_by_id(user_id):
    with SessionLocal() as db:
        user = db.query(User).filter(User.id == user_id).first()
        return user.__dict__.copy() if user else None

def list_users():
    with SessionLocal() as db:
        users = db.query(User).order_by(User.role, User.username).all()
        return [_user_public(u.__dict__) for u in users]
