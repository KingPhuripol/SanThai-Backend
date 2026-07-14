from datetime import datetime
from typing import Optional, List

from sqlalchemy import (
    Integer, String, Text, Float, Boolean,
    DateTime, ForeignKey, JSON, func,
)
from sqlalchemy.orm import mapped_column, Mapped, relationship
from pgvector.sqlalchemy import Vector

from app.database import Base


# ─── Communities ─────────────────────────────────────────────────────────────

class Community(Base):
    __tablename__ = "communities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    province: Mapped[str] = mapped_column(String(100))
    region: Mapped[str] = mapped_column(String(50))          # north/northeast/central/south
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    artisans: Mapped[List["Artisan"]] = relationship(back_populates="community")
    fabric_patterns: Mapped[List["FabricPattern"]] = relationship(back_populates="community")


# ─── Artisans ────────────────────────────────────────────────────────────────

class Artisan(Base):
    __tablename__ = "artisans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id"))
    bio_th: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bio_en: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    community: Mapped["Community"] = relationship(back_populates="artisans")
    fabric_patterns: Mapped[List["FabricPattern"]] = relationship(back_populates="artisan")


# ─── Fabric Patterns ─────────────────────────────────────────────────────────

class FabricPattern(Base):
    __tablename__ = "fabric_patterns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name_th: Mapped[str] = mapped_column(String(200))
    name_en: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    artisan_id: Mapped[int] = mapped_column(ForeignKey("artisans.id"))
    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id"))

    # Textile properties
    weave_technique: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    dye_method: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    fiber_type: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # Cultural
    cultural_meaning_th: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cultural_meaning_en: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    usage_rights: Mapped[str] = mapped_column(String(50), default="commercial")

    # AI-generated fields
    story_tags: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    ai_processed: Mapped[bool] = mapped_column(Boolean, default=False)

    # Media
    image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Vectors  (pgvector)
    clip_embedding: Mapped[Optional[list]] = mapped_column(Vector(512), nullable=True)
    text_embedding: Mapped[Optional[list]] = mapped_column(Vector(384), nullable=True)

    # Blockchain anchor
    genesis_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    artisan: Mapped["Artisan"] = relationship(back_populates="fabric_patterns")
    community: Mapped["Community"] = relationship(back_populates="fabric_patterns")
    provenance_logs: Mapped[List["ProvenanceLog"]] = relationship(back_populates="fabric")
    products: Mapped[List["Product"]] = relationship(back_populates="fabric")


# ─── Provenance (blockchain-sim) ─────────────────────────────────────────────

class ProvenanceLog(Base):
    __tablename__ = "provenance_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fabric_id: Mapped[int] = mapped_column(ForeignKey("fabric_patterns.id"))
    event_type: Mapped[str] = mapped_column(String(50))   # raw_material/dyeing/weaving/finished/sold
    actor_name: Mapped[str] = mapped_column(String(200))
    location: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    description_th: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description_en: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    prev_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    current_hash: Mapped[str] = mapped_column(String(64))
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    fabric: Mapped["FabricPattern"] = relationship(back_populates="provenance_logs")


# ─── Products ────────────────────────────────────────────────────────────────

class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fabric_id: Mapped[int] = mapped_column(ForeignKey("fabric_patterns.id"))
    artisan_id: Mapped[int] = mapped_column(ForeignKey("artisans.id"))
    title_th: Mapped[str] = mapped_column(String(300))
    title_en: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    description_th: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description_en: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price_thb: Mapped[float] = mapped_column(Float)
    price_usd: Mapped[float] = mapped_column(Float)
    stock: Mapped[int] = mapped_column(Integer, default=1)
    images: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    fabric: Mapped["FabricPattern"] = relationship(back_populates="products")
    artisan: Mapped["Artisan"] = relationship()
    orders: Mapped[List["Order"]] = relationship(back_populates="product")


# ─── Orders ──────────────────────────────────────────────────────────────────

class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    buyer_email: Mapped[str] = mapped_column(String(200))
    buyer_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    total_thb: Mapped[float] = mapped_column(Float)
    total_usd: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    product: Mapped["Product"] = relationship(back_populates="orders")


# ─── Quiz Sessions ───────────────────────────────────────────────────────────

class QuizSession(Base):
    __tablename__ = "quiz_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_token: Mapped[str] = mapped_column(String(100), unique=True)
    answers: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    recommended_fabric_ids: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ─── Chat History ────────────────────────────────────────────────────────────

class ChatHistory(Base):
    __tablename__ = "chat_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(100), index=True)
    role: Mapped[str] = mapped_column(String(20))   # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
