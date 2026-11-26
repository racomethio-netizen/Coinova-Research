"""multi-asset instruments: asset class, instrument type and contract multiplier on
trading_pairs (Stage 2)

Existing rows are crypto spot pairs with a multiplier of 1 (the defaults), so nothing
that works today changes. Values are restricted by CHECK constraints.

Revision ID: 0021_multi_asset_instruments
Revises: 0020_adaptive_grid
Create Date: 2026-11-26
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_multi_asset_instruments"
down_revision: str | None = "0020_adaptive_grid"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ASSET_CLASSES = ("crypto", "stocks", "commodities", "forex")
INSTRUMENT_TYPES = ("spot", "perpetual", "stock", "etf", "future", "fx_spot", "cfd", "index")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.add_column("trading_pairs", sa.Column("asset_class", sa.String(16), nullable=False,
                                             server_default="crypto"))
    op.add_column("trading_pairs", sa.Column("instrument_type", sa.String(16), nullable=False,
                                             server_default="spot"))
    op.add_column("trading_pairs", sa.Column("contract_multiplier", sa.Numeric(38, 18),
                                             nullable=False, server_default="1"))
    op.create_check_constraint("asset_class_valid", "trading_pairs",
                               _in("asset_class", ASSET_CLASSES))
    op.create_check_constraint("instrument_type_valid", "trading_pairs",
                               _in("instrument_type", INSTRUMENT_TYPES))
    op.create_check_constraint("contract_multiplier_positive", "trading_pairs",
                               "contract_multiplier > 0")


def downgrade() -> None:
    for name in ("contract_multiplier_positive", "instrument_type_valid", "asset_class_valid"):
        op.drop_constraint(name, "trading_pairs", type_="check")
    for col in ("contract_multiplier", "instrument_type", "asset_class"):
        op.drop_column("trading_pairs", col)
