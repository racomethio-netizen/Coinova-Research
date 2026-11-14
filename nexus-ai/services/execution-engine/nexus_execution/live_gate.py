"""The ONLY way to obtain a LiveBroker. Every gate must pass; none is set by code.

Gates:
1. Configuration (``Settings.live_trading_config_problems``): mode, flag, exact
   acknowledgement phrase, production env, no sandbox.
2. A current owner approval in the append-only audit log: the latest of
   ``live_trading_approved`` / ``live_trading_revoked`` by a ``user:`` actor is an
   approval, made within the last ``APPROVAL_MAX_AGE``.
3. Kill switch not engaged (fails closed if unreadable).
4. Every exchange to be traded has an active production credential whose withdrawal
   permission is verified disabled (or owner-confirmed where the API can't report it).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from nexus_common.timeutil import utcnow
from nexus_config import Settings
from nexus_db import models
from nexus_exchange.base import ExchangeAdapter
from nexus_risk.kill_switch import KillSwitch
from sqlalchemy import select
from sqlalchemy.orm import Session

GATE_TOKEN = object()
APPROVAL_MAX_AGE = timedelta(days=30)


def approval_problems(s: Session) -> list[str]:
    last = s.scalar(select(models.AuditLog).where(
        models.AuditLog.action.in_(("live_trading_approved", "live_trading_revoked")))
        .order_by(models.AuditLog.id.desc()).limit(1))
    if last is None:
        return ["no owner approval recorded"]
    if last.action != "live_trading_approved":
        return ["owner approval was revoked"]
    if not last.actor.startswith("user:"):
        return ["approval not made by a user"]
    if utcnow() - last.created_at > APPROVAL_MAX_AGE:
        return ["owner approval expired (older than 30 days)"]
    return []


def credential_problems(s: Session, exchanges: list[str]) -> list[str]:
    problems = []
    for code in exchanges:
        cred = s.scalar(select(models.ExchangeCredential).join(models.Exchange).where(
            models.Exchange.code == code, models.ExchangeCredential.environment == "production",
            models.ExchangeCredential.revoked_at.is_(None)))
        if cred is None:
            problems.append(f"{code}: no active production API credential")
        elif not cred.withdrawal_disabled_verified:
            problems.append(f"{code}: withdrawal permission not verified disabled")
    return problems


async def live_gate_problems(settings: Settings, s: Session, redis: Any,
                             exchanges: list[str]) -> list[str]:
    problems = list(settings.live_trading_config_problems())
    problems += approval_problems(s)
    if await KillSwitch(redis).is_engaged():
        problems.append("kill switch engaged")
    problems += credential_problems(s, exchanges)
    return problems


def credentialed_adapters(settings: Settings, s: Session) -> dict[str, ExchangeAdapter]:
    """Decrypt stored API keys (AES-GCM, context-bound) and build trading adapters.
    Keys never leave this function except inside the ccxt client."""
    from nexus_config import ExchangeCode
    from nexus_exchange.factory import build_adapter
    from nexus_security import CredentialCipher, KeyRing

    if settings.credential_encryption_keys is None:
        raise PermissionError("NEXUS_CREDENTIAL_ENCRYPTION_KEYS not configured")
    cipher = CredentialCipher(KeyRing.parse(settings.credential_encryption_keys.get_secret_value()))
    adapters: dict[str, ExchangeAdapter] = {}
    for code in settings.enabled_exchanges:
        cred = s.scalar(select(models.ExchangeCredential).join(models.Exchange).where(
            models.Exchange.code == code.value,
            models.ExchangeCredential.environment == "production",
            models.ExchangeCredential.revoked_at.is_(None)))
        if cred is None:
            continue
        ctx = {"user_id": str(cred.user_id), "exchange": code.value,
               "credential_id": str(cred.id)}
        adapters[code.value] = build_adapter(
            ExchangeCode(code.value),
            api_key=cipher.decrypt(cred.api_key_encrypted, context={**ctx, "field": "api_key"}),
            secret=cipher.decrypt(cred.api_secret_encrypted,
                                  context={**ctx, "field": "api_secret"}),
            ca_file=settings.extra_ca_file)
    if not adapters:
        raise PermissionError("no exchange credentials configured for live trading")
    return adapters


async def build_live_broker(settings: Settings, s: Session, redis: Any,
                            adapters: dict[str, ExchangeAdapter]) -> Any:
    from nexus_execution.brokers import LiveBroker

    problems = await live_gate_problems(settings, s, redis, list(adapters))
    if problems:
        raise PermissionError("live trading not permitted: " + "; ".join(problems))
    return LiveBroker(adapters, _gate_token=GATE_TOKEN)
