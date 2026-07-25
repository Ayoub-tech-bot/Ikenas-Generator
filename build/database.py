import os
import json
from datetime import datetime, timezone
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, Text, ForeignKey, UniqueConstraint, Index, select, func, update, delete
)
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.db")
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DB_PATH}")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# SQLite specific config
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    password_salt = Column(String, nullable=False)
    role = Column(String, nullable=False)
    niveaux_assignes = Column(Text, nullable=False, default="[]")
    quota_type = Column(String, nullable=False, default="unlimited")
    quota_value = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(String, nullable=False)
    created_by = Column(Integer, nullable=True)
    quota_daily = Column(Integer, nullable=True)
    quota_weekly = Column(Integer, nullable=True)
    quota_monthly = Column(Integer, nullable=True)
    school_id = Column(Integer, nullable=True)
    allowed_types = Column(Text, nullable=True)
    allowed_sujets = Column(Text, nullable=True)
    allowed_providers = Column(Text, nullable=True)
    can_use_own_key = Column(Boolean, nullable=False, default=True)
    permissions = Column(Text, nullable=True)

class Session(Base):
    __tablename__ = "sessions"
    token = Column(String, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)
    created_at = Column(String, nullable=False)
    expires_at = Column(String, nullable=False)

class UsageDaily(Base):
    __tablename__ = "usage_daily"
    user_id = Column(Integer, primary_key=True)
    date = Column(String, primary_key=True)
    tokens_used = Column(Integer, nullable=False, default=0)
    exercises_count = Column(Integer, nullable=False, default=0)

class KVStore(Base):
    __tablename__ = "kv_store"
    key = Column(String, primary_key=True, index=True)
    value = Column(Text, nullable=False)

class OrgApiKey(Base):
    __tablename__ = "org_api_keys"
    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String, nullable=False)
    school_id = Column(Integer, nullable=True)
    api_key = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)
    updated_by = Column(Integer, nullable=True)
    __table_args__ = (UniqueConstraint('provider', 'school_id', name='uix_provider_school'),)

class GenerationEvent(Base):
    __tablename__ = "generation_events"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)
    row_id = Column(String, nullable=False)
    niveau = Column(String, nullable=False)
    unite = Column(String, nullable=False)
    section = Column(String, nullable=False)
    sujet = Column(String, nullable=False)
    titre = Column(String, nullable=False)
    ex_type = Column(String, nullable=False)
    ex_variante = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    created_at = Column(String, nullable=False)
    school_id = Column(Integer, nullable=True)

class School(Base):
    __tablename__ = "schools"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    created_at = Column(String, nullable=False)
    created_by = Column(Integer, nullable=True)

class VaultItem(Base):
    __tablename__ = "vault_items"
    id = Column(Integer, primary_key=True, index=True)
    school_id = Column(Integer, nullable=False)
    author_id = Column(Integer, nullable=False)
    author_name = Column(String, nullable=False)
    row_id = Column(String, nullable=True)
    niveau = Column(String, nullable=False)
    unite = Column(String, nullable=False)
    section = Column(String, nullable=False)
    sujet = Column(String, nullable=False)
    titre = Column(String, nullable=False)
    ex_type = Column(String, nullable=False)
    ex_variante = Column(String, nullable=False)
    contenu_b64 = Column(Text, nullable=False)
    preview_text = Column(String, nullable=False)
    save_count = Column(Integer, nullable=False, default=0)
    created_at = Column(String, nullable=False)
    unpublished_at = Column(String, nullable=True)

class VaultSave(Base):
    __tablename__ = "vault_saves"
    id = Column(Integer, primary_key=True, index=True)
    vault_item_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    created_at = Column(String, nullable=False)
    __table_args__ = (UniqueConstraint('vault_item_id', 'user_id', name='uix_vault_user'),)

def init_db():
    Base.metadata.create_all(bind=engine)
