"""Correlated-exposure limit: positions that move together count as one risk.

A new entry is refused if the exposure to the new asset plus all held assets whose return
correlation with it is at or above ``threshold`` would exceed ``max_fraction`` of equity.
"""

from __future__ import annotations

from decimal import Decimal


def correlated_exposure_ok(*, new_asset: str, new_notional: Decimal, equity: Decimal,
                           held: dict[str, Decimal], correlations: dict[tuple[str, str], float],
                           threshold: float = 0.7,
                           max_fraction: Decimal = Decimal("0.35")) -> tuple[bool, str]:
    if equity <= 0:
        return False, "non-positive equity"
    group = new_notional
    names = []
    for asset, notional in held.items():
        rho = correlations.get((new_asset, asset), correlations.get((asset, new_asset)))
        if rho is not None and rho >= threshold:
            group += notional
            names.append(asset)
    if group > equity * max_fraction:
        return False, (f"correlated exposure {group} with {names or ['none']} exceeds "
                       f"{max_fraction} of equity")
    return True, "ok"
