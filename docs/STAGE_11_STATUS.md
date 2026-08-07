# Этап 11: события и тематическая кластеризация

Статус: реализация завершена в ветке, финальная CI-проверка выполняется.

Geo Analyzer 2 не изменяется.

## Реализовано

- отдельный пакет `soika_uds.events`;
- production handler `PipelineStage.EVENTS`;
- строгий join preprocessing/classification/geolocation/filtering по `message_key`;
- уровни `building`, `link`, `road`, `global`;
- разделённые embedding, dimensionality reduction и clustering interfaces;
- embeddings рассчитываются один раз, mutable topic model между scopes не переиспользуется;
- новый UMAP/HDBSCAN создаётся для каждого scope;
- фиксированный random seed;
- explicit minimum scope/event sizes;
- штатная обработка одного кластера, отсутствия кластеров и insufficient data;
- `message_ids` сохраняются JSON-массивами;
- event ID и все digests детерминированы;
- объяснение состава каждого события;
- разные темы по одному адресу не объединяются только по spatial identity;
- risk/connections исключены из этапа 11 и оставлены этапу 12;
- версия пакета `0.16.0`.

## Критерий завершения

Критерий будет подтверждён после успешных Python compilation, Ruff, полного deterministic unit suite, dependency-lock, CPU Docker health/readiness, GPU target build и закрытия всех review threads.

Подробности: `docs/EVENT_CLUSTERING.md`.
