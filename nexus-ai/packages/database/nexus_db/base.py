"""Declarative base, shared column types and constraint naming."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from sqlalchemy import BigInteger, DateTime, Identity, MetaData, Numeric, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Amounts and prices: up to 20 integer digits and 18 fractional digits (covers
# 18-decimal tokens). Never float.
Money = Numeric(38, 18)
# Ratios such as probabilities and percentages.
Ratio = Numeric(20, 10)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {  # noqa: RUF012 (SQLAlchemy reads this as a plain attribute)
        datetime: DateTime(timezone=True),
        Decimal: Money,
        dict[str, Any]: JSONB,
        list[Any]: JSONB,
    }


bigint_pk = Annotated[
    int, mapped_column(BigInteger, Identity(always=True), primary_key=True)
]
created_at = Annotated[
    datetime, mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
]
updated_at = Annotated[
    datetime,
    mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    ),
]

TRADING_MODES = ("paper", "live")


def in_list(column: str, values: tuple[str, ...]) -> str:
    """SQL text for a CHECK constraint restricting a text column to a closed set."""
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"
