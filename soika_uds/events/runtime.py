"""Reproducible multi-level event clustering runtime."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .components import ClusteringBackend, EmbeddingBackend, ReductionBackend
from .models import (
    EventBatchResult,
    EventBatchStats,
    EventCluster,
    EventClusteringConfig,
    EventLevel,
    EventMessage,
    ScopeDiagnostic,
    ScopeStatus,
    digest_json,
)

_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё-]+", re.UNICODE)
_STOPWORDS = frozenset(
    {
        "это",
        "как",
        "для",
        "что",
        "при",
        "или",
        "его",
        "еще",
        "ещё",
        "нет",
        "все",
        "всё",
        "она",
        "они",
        "оно",
        "уже",
        "так",
        "там",
        "тут",
        "где",
        "когда",
        "очень",
        "после",
        "перед",
        "через",
        "from",
        "with",
        "that",
        "this",
        "have",
        "has",
        "the",
        "and",
        "for",
    }
)
_LEVEL_ORDER = {level: index for index, level in enumerate(EventLevel)}


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _dominant(values: Sequence[str | None]) -> str | None:
    present = [value for value in values if value]
    if not present:
        return None
    counts = Counter(present)
    return min(counts, key=lambda value: (-counts[value], value.casefold(), value))


def _keywords(messages: Sequence[EventMessage], limit: int) -> tuple[str, ...]:
    counts: Counter[str] = Counter()
    for message in messages:
        seen: set[str] = set()
        for token in _TOKEN_RE.findall(message.model_text.casefold()):
            if len(token) < 3 or token in _STOPWORDS or token in seen:
                continue
            counts[token] += 1
            seen.add(token)
    ordered = sorted(counts, key=lambda value: (-counts[value], value))
    return tuple(ordered[:limit])


def _representatives(
    messages: Sequence[EventMessage],
    embeddings: Mapping[str, Sequence[float]],
    limit: int,
) -> tuple[str, ...]:
    if len(messages) <= limit:
        return tuple(sorted(message.message_key for message in messages))
    rows = [embeddings[message.message_key] for message in messages]
    width = len(rows[0])
    centroid = tuple(sum(row[index] for row in rows) / len(rows) for index in range(width))
    ranked = sorted(
        messages,
        key=lambda message: (
            -_cosine(embeddings[message.message_key], centroid),
            message.message_key,
        ),
    )
    return tuple(message.message_key for message in ranked[:limit])


def _time_bounds(messages: Sequence[EventMessage]) -> tuple[str | None, str | None]:
    values = sorted(
        message.published_at_utc
        for message in messages
        if message.published_at_utc is not None
    )
    if not values:
        return None, None
    return values[0], values[-1]


@dataclass(frozen=True, slots=True)
class EventClusteringEngine:
    embedder: EmbeddingBackend
    reducer: ReductionBackend
    clusterer: ClusteringBackend
    config: EventClusteringConfig = field(default_factory=EventClusteringConfig)

    def _event(
        self,
        *,
        level: EventLevel,
        object_id: str,
        cluster_label: int,
        messages: Sequence[EventMessage],
        embeddings: Mapping[str, Sequence[float]],
    ) -> EventCluster:
        ordered = tuple(sorted(messages, key=lambda item: item.message_key))
        message_ids = tuple(message.message_key for message in ordered)
        category = _dominant([message.category for message in ordered])
        topic = _dominant([message.topic for message in ordered])
        keywords = _keywords(ordered, self.config.keyword_limit)
        representatives = _representatives(
            ordered,
            embeddings,
            self.config.representative_limit,
        )
        started_at, ended_at = _time_bounds(ordered)
        event_digest = digest_json(
            {
                "algorithm_version": self.config.algorithm_version,
                "level": level.value,
                "object_id": object_id,
                "message_ids": list(message_ids),
            }
        )
        return EventCluster(
            event_id=f"evt_{event_digest[:24]}",
            level=level,
            object_id=object_id,
            message_ids=message_ids,
            category=category,
            topic=topic,
            keywords=keywords,
            representative_message_ids=representatives,
            started_at=started_at,
            ended_at=ended_at,
            explanation={
                "basis": ["shared_spatial_scope", "semantic_cluster_assignment"],
                "cluster_label": cluster_label,
                "member_count": len(message_ids),
                "dominant_category": category,
                "dominant_topic": topic,
                "keywords": list(keywords),
                "representative_message_ids": list(representatives),
                "address_only_merge_prohibited": True,
            },
        )

    def cluster(self, messages: Sequence[EventMessage]) -> EventBatchResult:
        if isinstance(messages, str | bytes | bytearray) or not isinstance(messages, Sequence):
            raise TypeError("messages must be an array")
        ordered = tuple(sorted(messages, key=lambda item: item.message_key))
        if not all(isinstance(item, EventMessage) for item in ordered):
            raise TypeError("messages must contain EventMessage values")
        keys = [message.message_key for message in ordered]
        if len(keys) != len(set(keys)):
            raise ValueError("message_key values must be unique")

        if ordered:
            embedding_batch = self.embedder.embed(
                tuple(message.model_text for message in ordered)
            )
            if len(embedding_batch.vectors) != len(ordered):
                raise ValueError("embedder must return one vector per message")
            embedding_provenance = dict(embedding_batch.provenance)
            embeddings = {
                message.message_key: embedding_batch.vectors[index]
                for index, message in enumerate(ordered)
            }
        else:
            embedding_provenance = {
                "component": "not_run",
                "reason": "no_eligible_messages",
            }
            embeddings = {}

        events: list[EventCluster] = []
        diagnostics: list[ScopeDiagnostic] = []
        scope_components: dict[str, Any] = {}

        for level in self.config.levels:
            groups: dict[str, list[EventMessage]] = {}
            unresolved: list[EventMessage] = []
            for message in ordered:
                object_id = message.scopes.get(level.value)
                if object_id is None:
                    unresolved.append(message)
                else:
                    groups.setdefault(object_id, []).append(message)
            if unresolved:
                diagnostics.append(
                    ScopeDiagnostic(
                        level=level,
                        object_id="__unresolved__",
                        message_count=len(unresolved),
                        status=ScopeStatus.UNAVAILABLE,
                        cluster_count=0,
                        noise_count=0,
                        reasons=("scope_identifier_unavailable",),
                    )
                )

            for object_id in sorted(groups):
                scoped = tuple(sorted(groups[object_id], key=lambda item: item.message_key))
                if len(scoped) < self.config.min_scope_messages:
                    diagnostics.append(
                        ScopeDiagnostic(
                            level=level,
                            object_id=object_id,
                            message_count=len(scoped),
                            status=ScopeStatus.INSUFFICIENT_DATA,
                            cluster_count=0,
                            noise_count=0,
                            reasons=("scope_below_minimum_message_count",),
                        )
                    )
                    continue

                source_vectors = tuple(embeddings[item.message_key] for item in scoped)
                reduced = self.reducer.reduce(source_vectors, seed=self.config.random_seed)
                if len(reduced.vectors) != len(scoped):
                    raise ValueError("reducer must preserve the message count")
                assignment = self.clusterer.cluster(
                    reduced.vectors,
                    min_cluster_size=self.config.min_event_size,
                    allow_single_cluster=self.config.allow_single_cluster,
                    seed=self.config.random_seed,
                )
                if len(assignment.labels) != len(scoped):
                    raise ValueError("clusterer must return one label per message")
                scope_key = f"{level.value}:{object_id}"
                scope_components[scope_key] = {
                    "reduction": dict(reduced.provenance),
                    "clustering": dict(assignment.provenance),
                }

                grouped_labels: dict[int, list[EventMessage]] = {}
                noise_count = 0
                for message, label in zip(scoped, assignment.labels, strict=True):
                    if label == -1:
                        noise_count += 1
                        if not self.config.include_noise:
                            continue
                    grouped_labels.setdefault(label, []).append(message)
                grouped_labels = {
                    label: members
                    for label, members in grouped_labels.items()
                    if len(members) >= self.config.min_event_size
                }
                if not grouped_labels:
                    diagnostics.append(
                        ScopeDiagnostic(
                            level=level,
                            object_id=object_id,
                            message_count=len(scoped),
                            status=ScopeStatus.NO_CLUSTERS,
                            cluster_count=0,
                            noise_count=noise_count,
                            reasons=("clustering_produced_no_eligible_events",),
                        )
                    )
                    continue
                if len(grouped_labels) > self.config.max_events_per_scope:
                    raise ValueError("cluster count exceeds max_events_per_scope")

                for label in sorted(grouped_labels):
                    events.append(
                        self._event(
                            level=level,
                            object_id=object_id,
                            cluster_label=label,
                            messages=grouped_labels[label],
                            embeddings=embeddings,
                        )
                    )
                diagnostics.append(
                    ScopeDiagnostic(
                        level=level,
                        object_id=object_id,
                        message_count=len(scoped),
                        status=ScopeStatus.CLUSTERED,
                        cluster_count=len(grouped_labels),
                        noise_count=noise_count,
                        reasons=("semantic_clusters_created",),
                    )
                )

        events_tuple = tuple(sorted(events, key=lambda item: item.event_id))
        diagnostics_tuple = tuple(
            sorted(
                diagnostics,
                key=lambda item: (_LEVEL_ORDER[item.level], item.object_id, item.status.value),
            )
        )
        stats = EventBatchStats(
            received=len(ordered),
            eligible=len(ordered),
            events=len(events_tuple),
            event_memberships=sum(item.size for item in events_tuple),
            clustered_scopes=sum(
                item.status is ScopeStatus.CLUSTERED for item in diagnostics_tuple
            ),
            insufficient_scopes=sum(
                item.status is ScopeStatus.INSUFFICIENT_DATA for item in diagnostics_tuple
            ),
            no_cluster_scopes=sum(
                item.status is ScopeStatus.NO_CLUSTERS for item in diagnostics_tuple
            ),
            unavailable_scopes=sum(
                item.status is ScopeStatus.UNAVAILABLE for item in diagnostics_tuple
            ),
        )
        input_digest = digest_json([message.to_dict() for message in ordered])
        component_provenance = {
            "embedding": embedding_provenance,
            "scope_components": scope_components,
            "random_seed": self.config.random_seed,
            "mutable_topic_model_reuse": False,
        }
        output_core = {
            "events": [item.to_dict() for item in events_tuple],
            "diagnostics": [item.to_dict() for item in diagnostics_tuple],
            "stats": stats.to_dict(),
            "input_digest": input_digest,
            "config_digest": self.config.digest,
            "component_provenance": component_provenance,
        }
        return EventBatchResult(
            events=events_tuple,
            diagnostics=diagnostics_tuple,
            stats=stats,
            input_digest=input_digest,
            output_digest=digest_json(output_core),
            config_digest=self.config.digest,
            component_provenance=component_provenance,
            schema_version=self.config.schema_version,
            algorithm_version=self.config.algorithm_version,
        )
