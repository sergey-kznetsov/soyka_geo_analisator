"Strict source research and policy manifest loading."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .models import SourcePolicy, SourcePolicyError


def load_source_policy(path: Path) -> SourcePolicy:
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SourcePolicyError(f"cannot read source policy {path}: {error}") from error
    if not isinstance(payload, Mapping):
        raise SourcePolicyError("source policy document must be a JSON object")
    return SourcePolicy.from_dict(payload)


def validate_source_policy_document(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    return SourcePolicy.from_dict(payload).to_dict()


def source_policy_summary(policy: SourcePolicy) -> dict[str, Any]:
    return {
        "source_id": policy.source_id,
        "display_name": policy.display_name,
        "owner": policy.owner,
        "access_method": policy.access_method.value,
        "permission_status": policy.permission_status.value,
        "enabled": policy.enabled,
        "reviewed_at": (
            policy.reviewed_at.isoformat().replace("+00:00", "Z")
            if policy.reviewed_at is not None
            else None
        ),
        "review_due_at": (
            policy.review_due_at.isoformat().replace("+00:00", "Z")
            if policy.review_due_at is not None
            else None
        ),
        "jurisdictions": list(policy.jurisdictions),
        "data_categories": [item.value for item in policy.data.categories],
        "retention_days": policy.data.retention_days,
        "robots_requirement": policy.robots_requirement.value,
        "allowed_domains": list(policy.security.allowed_domains),
        "parser_version": policy.parser_version,
    }
