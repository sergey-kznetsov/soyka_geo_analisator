# Этап 12: связи, показатели и риск

Статус: техническая реализация завершена; внешний expert-validation gate остаётся открытым.

Geo Analyzer 2 не изменялся.

## Реализовано

Создан отдельный production-контур `soika_uds.scoring` и handler `PipelineStage.SCORING`.

Связи строятся по точному пересечению коллекций `message_ids`, имеют Jaccard weight и canonical ordering. Геометрия связи остаётся GeoJSON в `OGC:CRS84`; метрическое расстояние рассчитывается после реальной coordinate transform через `pyproj` в локальную UTM CRS.

Наблюдаемые показатели отделены от итогового score. Baseline 1.0.0 использует intensity, persistence, unique external connectivity и spatial spread. Для каждого показателя сохраняются raw/reference/normalized/weight/contribution/status/reason.

Dataset-relative min-max удалён. Нормализация использует фиксированные положительные references, поэтому zero-range division невозможен. Отсутствующее наблюдение делает score `unavailable`, а не равным нулю.

Nested building/link/road/global events не увеличивают connectivity повторно: показатель основан на множестве уникальных внешних message IDs.

Формула, веса, references и thresholds версионированы и входят в `config_digest`. Версия пакета — `0.17.0`.

## Expert-validation gate

Репозиторий не содержит вымышленного экспертного одобрения. `ExpertValidationManifest` требует реальный `review_id`, роль эксперта, дату, evidence SHA-256, formula version и config digest.

До получения такого evidence:

- score может использоваться для технической проверки воспроизводимости;
- `decision_use_approved=false`;
- `PipelineStage.SCORING` выдаёт `RISK_FORMULA_NOT_EXPERT_VALIDATED`;
- baseline нельзя считать экспертно подтверждённой управленческой моделью риска.

При изменении формулы или конфигурации существующий manifest автоматически перестаёт подтверждать decision use.

## Проверка

Первый полный code CI подтвердил:

- Python compilation — passed;
- Ruff — passed;
- 253 deterministic unit/orchestration tests — passed;
- `poetry.lock` consistency — passed.

CPU/GPU container check и финальный automated review выполняются на PR этапа 12. После них статус будет обновлён фактическими результатами.

## Критерий

Технический критерий этапа выполнен: одинаковый набор событий даёт детерминированный, проверяемый и объяснимый результат; неизвестные данные не маскируются нулевым риском.

Внешний критерий экспертной валидации остаётся release-gate и не может быть закрыт без реального экспертного evidence.

Следующий технический этап по плану — этап 13: хранилище и кеш.

Подробности: `docs/RISK_SCORING.md`.
