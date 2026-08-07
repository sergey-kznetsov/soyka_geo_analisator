# Этап 12: связи, показатели и риск

Статус: технический этап завершён; внешний expert-validation gate остаётся fail-closed.

Geo Analyzer 2 не изменялся.

## Реализовано

Создан отдельный production-контур `soika_uds.scoring` и handler `PipelineStage.SCORING`.

Связи строятся только по точному пересечению коллекций `message_ids`, имеют Jaccard weight и canonical ordering. Идентификаторы рассматриваются как целые строки, поэтому legacy-дефект посимвольного пересечения исключён.

Геометрия связи остаётся GeoJSON `LineString` в `OGC:CRS84`. Метрическое расстояние рассчитывается после реальной coordinate transform через `pyproj`. Выбор центра долготы использует circular mean, поэтому события и связи, пересекающие антимеридиан, не получают искусственно глобальный spatial spread. За пределами UTM используется локальная Azimuthal Equidistant CRS вместо Web Mercator, поэтому полярные distance/spread расчёты остаются метрическими и локальными.

Наблюдаемые показатели отделены от итогового score. Baseline 1.0.0 использует intensity, persistence, unique external connectivity и spatial spread. Для каждого показателя сохраняются `raw_value`, `reference_value`, `normalized_value`, `weight`, `contribution`, `status` и `reason`.

Dataset-relative min-max удалён. Нормализация использует фиксированные положительные references, поэтому zero-range division невозможен. Отсутствующее наблюдение делает score `unavailable`, а не равным нулю.

Nested building/link/road/global events не увеличивают connectivity повторно: показатель основан на множестве уникальных внешних `message_ids`.

Tolerance-accepted машинная погрешность суммы весов нормализуется в эффективный единичный бюджет до расчёта contributions. Поэтому итоговый score остаётся согласованным с суммой вкладов и ограниченным диапазоном `[0, 1]`.

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

Закрыты пять замечаний automated review:

1. stale expert manifest больше не публикуется как эффективное одобрение текущей formula/configuration;
2. выбор локальной проекции корректно работает для точек по разные стороны антимеридиана;
3. публичный `RiskScoringEngine.score()` валидирует каждый элемент `events` как `EventCluster` до чтения `event_id` и сортировки;
4. полярные metric calculations используют локальную Azimuthal Equidistant CRS, а не Web Mercator;
5. tolerance-accepted веса нормализуются до единичного effective budget, поэтому saturated score не может выйти за `[0, 1]`.

Все пять дефектов покрыты regression tests, review threads resolved.

## Финальная проверка кодового head

GitHub Actions подтвердил:

- Python compilation — passed;
- Ruff — passed;
- 260 deterministic unit/orchestration/regression tests — passed;
- `poetry.lock` consistency — passed на предыдущем неизменённом dependency graph и повторно проверяется на финальном doc-head;
- CPU/GPU container gate повторно проверяется на финальном doc-head.

## Критерий

Технический критерий этапа выполнен: один и тот же набор событий и точек даёт детерминированный, проверяемый и объяснимый результат независимо от порядка входа; связи используют точные идентификаторы и корректную CRS, нулевой диапазон нормализации не возникает, malformed public input обрабатывается контролируемо, веса не выводят score за единичный диапазон, а неизвестные данные не маскируются нулевым риском.

Экспертная валидация остаётся внешним release-gate. Она принципиально не может быть закрыта кодом или автоматическим тестом без реального экспертного evidence.

Следующий технический этап по плану — этап 13: хранилище и кеш.

Подробности: `docs/RISK_SCORING.md`.
