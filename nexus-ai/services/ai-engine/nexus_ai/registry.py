"""Model registry: versions, artifacts (sha256-verified) and evaluations in Postgres.

Status flow: training -> validated | rejected; validated -> active only by an explicit
owner action (API, audit-logged); active -> retired. A model is never activated
automatically, and never because of in-sample metrics.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from nexus_db import models, repo
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from nexus_ai.model import EnsembleModel

ARTIFACT_DIR = Path("research/training/artifacts")


def next_version(s: Session, name: str) -> str:
    count = len(s.scalars(select(models.AiModel.id).where(models.AiModel.name == name)).all())
    return f"{count + 1}"


def register(s: Session, *, name: str, algorithm: str, feature_set: dict[str, Any],
             hyperparameters: dict[str, Any], training_start: datetime | None,
             training_end: datetime | None, code_version: str | None) -> models.AiModel:
    m = models.AiModel(name=name, version=next_version(s, name), algorithm=algorithm,
                       status="training", feature_set=feature_set,
                       hyperparameters=hyperparameters, training_start=training_start,
                       training_end=training_end, code_version=code_version)
    s.add(m)
    s.flush()
    return m


def record_evaluation(s: Session, model: models.AiModel, *, evaluation_type: str,
                      period_start: datetime, period_end: datetime, metrics: dict[str, Any],
                      passed: bool, failures: list[str]) -> None:
    s.add(models.ModelEvaluation(model_id=model.id, evaluation_type=evaluation_type,
                                 period_start=period_start, period_end=period_end,
                                 metrics=metrics, passed=passed, failure_reasons=failures))
    model.status = "validated" if passed else "rejected"
    s.flush()


def save_artifact(s: Session, model_row: models.AiModel, model: EnsembleModel,
                  directory: Path = ARTIFACT_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    data = model.to_bytes()
    path = directory / f"{model_row.name}-v{model_row.version}.pkl"
    path.write_bytes(data)
    model_row.artifact_uri = str(path)
    model_row.artifact_sha256 = hashlib.sha256(data).hexdigest()
    s.flush()
    return path


def load_active(s: Session, name: str) -> tuple[models.AiModel, EnsembleModel] | None:
    row = s.scalar(select(models.AiModel).where(models.AiModel.name == name,
                                                models.AiModel.status == "active"))
    if row is None or not row.artifact_uri or not row.artifact_sha256:
        return None
    data = Path(row.artifact_uri).read_bytes()
    return row, EnsembleModel.from_bytes(data, expected_sha256=row.artifact_sha256)


def activate(s: Session, model_id: int, *, actor: str, now: datetime) -> None:
    """Owner-only promotion. Requires status 'validated'; retires the previous active."""
    if not actor.startswith("user:"):
        raise PermissionError("only the owner can activate a model")
    row = s.get(models.AiModel, model_id)
    if row is None or row.status != "validated":
        raise ValueError("only a validated model can be activated")
    s.execute(update(models.AiModel).where(models.AiModel.name == row.name,
                                           models.AiModel.status == "active")
              .values(status="retired"))
    row.status, row.promoted_at = "active", now
    repo.record_audit(s, actor=actor, action="model_activated", target_type="ai_model",
                      target_id=str(model_id), details={"name": row.name, "version": row.version})
    s.flush()


ROLLBACK_ACTORS = ("user:", "system:model-health")


def rollback(s: Session, name: str, *, actor: str, reason: str,
             now: datetime) -> models.AiModel | None:
    """Retire the active version of ``name`` and re-activate the most recently promoted
    earlier version (it passed validation when it was promoted). Allowed for the owner and
    for the model-health monitor (a rollback only returns to a previously validated model;
    it can never promote an unvalidated one). Returns the re-activated row, or None when
    there is no earlier version (then nothing is active: no model is better than a bad one).
    """
    if not actor.startswith(ROLLBACK_ACTORS):
        raise PermissionError("rollback needs the owner or the model-health monitor")
    current = s.scalar(select(models.AiModel).where(models.AiModel.name == name,
                                                    models.AiModel.status == "active"))
    if current is None:
        raise ValueError(f"no active version of {name}")
    previous = s.scalar(select(models.AiModel).where(
        models.AiModel.name == name, models.AiModel.status == "retired",
        models.AiModel.promoted_at.is_not(None), models.AiModel.artifact_sha256.is_not(None),
        models.AiModel.id != current.id).order_by(models.AiModel.promoted_at.desc()).limit(1))
    current.status = "retired"
    s.flush()
    if previous is not None:
        previous.status, previous.promoted_at = "active", now
    repo.record_audit(s, actor=actor, action="model_rolled_back", target_type="ai_model",
                      target_id=str(current.id),
                      details={"name": name, "from_version": current.version,
                               "to_version": previous.version if previous else None,
                               "reason": reason})
    s.flush()
    return previous
