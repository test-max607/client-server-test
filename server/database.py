"""SQLite schema and transaction boundaries shared by both APIs."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    event,
    text,
)
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class Meter(Base):
    __tablename__ = "meters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    serial_number: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    location: Mapped[str] = mapped_column(String, nullable=False)


class Reading(Base):
    __tablename__ = "readings"
    __table_args__ = (
        UniqueConstraint("meter_id", "recorded_at", name="uq_reading_meter_time"),
        CheckConstraint("reading_kwh >= 0", name="ck_reading_nonnegative"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meter_id: Mapped[int] = mapped_column(ForeignKey("meters.id"), nullable=False)
    recorded_at: Mapped[str] = mapped_column(String(20), nullable=False)
    reading_kwh: Mapped[float] = mapped_column(Float, nullable=False)


class Database:
    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            URL.create("sqlite", database=str(path)),
            connect_args={"check_same_thread": False, "timeout": 5},
        )

        @event.listens_for(self.engine, "connect")
        def configure_connection(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA busy_timeout = 5000")
            cursor.close()

        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(text("PRAGMA journal_mode = WAL"))
            connection.commit()
        Base.metadata.create_all(self.engine)

    @contextmanager
    def write(self) -> Iterator[Session]:
        # Acquire the single SQLite writer slot before reading for deduplication.
        with self.sessions() as session:
            try:
                session.execute(text("BEGIN IMMEDIATE"))
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    def close(self) -> None:
        self.engine.dispose()
