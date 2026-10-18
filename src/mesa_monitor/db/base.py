"""Base declarativa de SQLAlchemy y tipos comunes."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Nombres de constraints deterministas para que Alembic genere migraciones estables.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def str_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """Enum guardado como texto + CHECK (no enum nativo de Postgres: más fácil de migrar)."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )


def utc_ts() -> DateTime:
    return DateTime(timezone=True)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(utc_ts(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        utc_ts(), server_default=func.now(), onupdate=func.now()
    )
