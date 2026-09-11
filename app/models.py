from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    github_id: Mapped[str] = mapped_column(String(100), unique=True)
    username: Mapped[str] = mapped_column(String(100))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    email: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Repository(Base):
    __tablename__ = "repositories"
    __table_args__ = (UniqueConstraint("owner", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    owner: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(200))
    full_name: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    default_branch: Mapped[str] = mapped_column(String(100), default="main")
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    registered_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scans: Mapped[list["Scan"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )
    dependencies: Mapped[list["Dependency"]] = relationship(cascade="all, delete-orphan")


class Scan(Base):
    __tablename__ = "scans"
    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    freshness_score: Mapped[float | None] = mapped_column(Float)
    risk_score: Mapped[float | None] = mapped_column(Float)
    commit_sha_scanned: Mapped[str | None] = mapped_column(String(100))
    repository: Mapped[Repository] = relationship(back_populates="scans")
    snapshots: Mapped[list["DependencySnapshot"]] = relationship(cascade="all, delete-orphan")


class Dependency(Base):
    __tablename__ = "dependencies"
    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"))
    ecosystem: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(300))
    current_version_required: Mapped[str] = mapped_column(String(100))
    is_ignored: Mapped[bool] = mapped_column(Boolean, default=False)


class DependencySnapshot(Base):
    __tablename__ = "dependency_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id"))
    dependency_id: Mapped[int] = mapped_column(ForeignKey("dependencies.id"))
    latest_version: Mapped[str] = mapped_column(String(100), default="unknown")
    versions_behind: Mapped[int] = mapped_column(Integer, default=0)
    is_archived_upstream: Mapped[bool] = mapped_column(Boolean, default=False)
    days_since_last_release: Mapped[int] = mapped_column(Integer, default=0)
    known_cves: Mapped[list] = mapped_column(JSON, default=list)
    points_deducted: Mapped[float] = mapped_column(Float, default=0)
    lag_points: Mapped[float] = mapped_column(Float, default=0)
    age_points: Mapped[float] = mapped_column(Float, default=0)
    archived_points: Mapped[float] = mapped_column(Float, default=0)
    cve_points: Mapped[float] = mapped_column(Float, default=0)


class BadgeCache(Base):
    __tablename__ = "badge_cache"
    repo_full_name: Mapped[str] = mapped_column(String(300), primary_key=True)
    svg_body: Mapped[str] = mapped_column(Text)
    score: Mapped[float] = mapped_column(Float)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
