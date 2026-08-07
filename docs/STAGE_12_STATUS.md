# Этап 12: связи, показатели и риск

Статус: технический этап завершён; внешний expert-validation gate остаётся fail-closed.

Geo Analyzer 2 не изменялся.

## Реализовано

Создан отдельный production-контур `soika_uds.scoring` и handler `PipelineStage.SCORING`.

Связи строятся только по точному пересечению коллекций `message_ids`, имеют Jaccard weight и canonical ordering. Идентификаторы рассматриваются как целые строки, поэтому legacy-дефект посимвольного пересечения исключён.

Геометрия связи остаётся GeoJSON `LineString` в `OGC:CRS84`. Метрическое расстояние рассчитывается после реальной coordinate transform через `pyproj`. Выбор центра долготы использует circular mean, поэтому события и связи, пересекающие антимеридиан, не получают искусственно глобальный spatial spread.

Наблюдаемые показатели отделены от итогового score. Baseline 1.0.0 использует intensity, persistence, unique external connectivity и spatial spread. Для каждого показателя сохраняются `raw_value`, `reference_value`, `normalized_value`, `weight`, `contribution`, `status` и `reason`.

Dataset-relative min-max удалён. Нормализация использует фиксированные положительные references, поэтому zero-range division невозможен. Отсутствующее наблюдение делает score `unavailable`, а не равным нулю.

Nested building/link/road/global events не увеличивают connectivity повторно: показатель основан на множестве уникальных внешних `message_ids`.

Формула, веса, references и thresholds версионированы и входят в `config_digest`. Версия пакета — `0.17.0`.

## Expert-validation gate

Репозиторий не содержит вымышленного экспертного одобрения. `ExpertValidationManifest` связывает экспертный акт с `formula_version`, `config_digest`, `review_id`, ролью эксперта, датой и SHA-256 evidence.

Одного manifest недостаточно для decision-use. `decision_use_approved=true` возможно только когда одновременно:

- manifest имеет `approved=true`;
- `formula_version` и `config_digest` точно совпадают с выполняемой конфигурацией;
- внешний evidence verifier подтверждает evidence для этого manifest.

В `formula_validation.approved` публикуется только эффективный результат текущего gate. Исходное экспертное решение сохраняется отдельно как `manifest_approved`, поэтому устаревший manifest не может выглядеть действующим.

До получения реального внешнего evidence:

- score доступен для технической проверки воспроизводимости;
- `decision_use_approved=false`;
- `PipelineStage.SCORING` выдаёт `RISK_FORMULA_NOT_EXPERT_VALIDATED`;
- baseline нельзя трактовать как экспертно подтверждённую управленческую модель риска.

Изменение формулы или конфигурации меняет `config_digest` и автоматически требует нового экспертного подтверждения.

## Automated review

Закрыты оба замечания automated review:

1. stale expert manifest больше не публикуется как эффективное одобрение текущей formula/configuration;
2. выбор локальной проекции корректно работает для точек по разные стороны антимеридиана.

Оба дефекта покрыты отдельными regression tests, review threads resolved.

## Финальная проверка кодового head

GitHub Actions подтвердил:

- Python compilation — passed;
- Ruff — passed;
- 257 deterministic unit/orchestration/regression tests — passed;
- `poetry.lock` consistency — passed;
- CPU Docker image build — passed;
- CPU container start — passed;
- `/healthz` и `/readyz` — passed;
- GPU target build — passed.

## Критерий

Технический критерий этапа выполнен: один и тот же набор событий и точек даёт детерминированный, проверяемый и объяснимый результат независимо от порядка входа; связи используют точные идентификаторы и корректную CRS, нулевой диапазон нормализации не возникает, а неизвестные данные не маскируются нулевым риском.

Экспертная валидация остаётся внешним release-gate. Она принципиально не может быть закрыта кодом или автоматическим тестом без реального экспертного evidence.

Следующий технический этап по плану — этап 13: хранилище и кеш.

Подробности: `docs/RISK_SCORING.md`.
