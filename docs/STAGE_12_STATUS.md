# Этап 12: связи, показатели и риск

Статус: технический этап завершён; внешний expert-validation gate остаётся fail-closed.

Geo Analyzer 2 не изменялся.

## Реализовано

Создан production-контур `soika_uds.scoring` и handler `PipelineStage.SCORING`.

Связи строятся только по точному пересечению коллекций `message_ids`, имеют Jaccard weight и canonical ordering. GeoJSON остаётся в `OGC:CRS84`; distance/spread рассчитываются через global-safe WGS84 geodesic с локальной Azimuthal Equidistant CRS только для локальных наборов.

Антимеридианные connection geometries разрезаются на `+180/-180` и сериализуются как `MultiLineString`. Географически широкие события не помещаются в одну локальную проекцию: для них используется WGS84 geodesic spread с `event_metric_crs=null`.

Baseline 1.0.0 разделяет наблюдаемые `intensity`, `persistence`, `connectivity`, `spatial_spread`, их нормализованные значения и итоговый score. Dataset-relative min-max удалён; fixed positive references исключают zero-range division. Missing observation даёт `score=unavailable`, а не нулевой риск.

Nested building/link/road/global events не удваивают connectivity. Tolerance-accepted веса переводятся в effective unit budget через монотонно уменьшающийся `remaining`, поэтому floating-point residual не может сделать сумму effective weights больше 1.

Формула, веса, references и thresholds версионированы и входят в `config_digest`. Версия пакета — `0.17.0`.

## Expert-validation gate

Репозиторий не содержит вымышленного экспертного одобрения. `decision_use_approved=true` возможно только при matching approved manifest и успешной внешней проверке evidence. `formula_validation.approved` содержит только эффективный текущий gate, а исходное экспертное решение хранится отдельно как `manifest_approved`.

До получения реального внешнего expert evidence:

- score доступен для технической проверки;
- `decision_use_approved=false`;
- handler выдаёт `RISK_FORMULA_NOT_EXPERT_VALIDATED`;
- baseline нельзя трактовать как экспертно подтверждённую управленческую модель риска.

## Automated review

Закрыты восемь P2-замечаний automated review:

1. stale expert manifest не публикуется как эффективное одобрение;
2. antimeridian center не рассчитывается арифметическим средним долготы;
3. public `score()` проверяет `EventCluster` до доступа к `event_id`;
4. полярные metric calculations не используют Web Mercator;
5. tolerance-accepted веса не выводят saturated score за `[0,1]`;
6. residual rounding не переполняет effective weight budget;
7. connection GeoJSON через антимеридиан разрезается в `MultiLineString`;
8. глобально широкий point set использует WGS84 geodesic spread вместо одной локальной/UTM-проекции.

Все восемь замечаний покрыты regression tests, их review threads resolved.

## Проверка

На последнем кодовом head GitHub Actions подтвердил:

- Python compilation — passed;
- Ruff — passed;
- 262 deterministic unit/orchestration/regression tests — passed;
- `poetry.lock` consistency — passed;
- CPU Docker image build — passed после повторного запуска transient Docker Hub HTTP 522;
- CPU container start — passed;
- `/healthz` и `/readyz` — passed;
- GPU target build — passed.

## Критерий

Технический критерий этапа выполнен: одинаковый набор событий и точек даёт детерминированный, проверяемый и объяснимый результат независимо от порядка входа; связи используют точные identifiers и global-safe spatial calculations; missing data не маскируется нулём; floating-point веса не нарушают unit score budget.

Экспертная валидация остаётся внешним release-gate и не может быть закрыта кодом без реального expert evidence.

Следующий технический этап — этап 13: хранилище и кеш.

Подробности: `docs/RISK_SCORING.md`.
