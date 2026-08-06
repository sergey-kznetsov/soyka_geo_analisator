# Этап 8: классификация и уточнение тем

Статус: этапы 8A и 8B реализованы 6 августа 2026 года. Production activation конкретных legacy-моделей остаётся заблокированной до подтверждения всех qualification gates.

Geo Analyzer 2 не изменялся.

## Этап 8A: техническая production-платформа

Реализовано:

- отдельный пакет `soika_uds.classification`;
- строгий fail-closed model registry;
- обязательные immutable model/tokenizer revisions;
- явное production approval модели;
- неизменяемые classification contracts;
- пакетный inference;
- локальный Transformers backend;
- одинаковый контракт CPU/GPU;
- независимые category/topic thresholds;
- low-confidence decision и причины;
- identity и piecewise-linear confidence calibration;
- calibration validation digest;
- model, tokenizer, registry, config, device и calibration provenance;
- детерминированный output digest;
- handler `PipelineStage.NLP`;
- label distribution и total-variation drift;
- unit tests без сети и загрузки моделей.

Подробности: `docs/CLASSIFICATION.md`.

## Этап 8B: qualification и release gates

Реализовано:

- строгие model audit records для category/topic roles;
- обязательная проверка репозитория, immutable revision и SHA-256 весов;
- license, training-data и intended-use gates;
- allowlist безопасных форматов весов;
- validation-set manifest;
- контроль размера, category/topic coverage, annotation depth и agreement;
- CPU/GPU benchmark evidence;
- контроль repeat count и соответствия validation digest;
- проверка детерминированной эквивалентности CPU/GPU output;
- accuracy, macro-precision, macro-recall и macro-F1;
- per-label precision, recall, F1 и support;
- confusion matrices;
- Expected Calibration Error;
- Brier score;
- calibration-bin statistics;
- low-confidence, calibration и drift gates;
- обязательные calibration и baseline digests;
- детерминированные input/report digests;
- строгий JSON loader;
- qualification CLI;
- машиночитаемый legacy audit;
- тесты успешного и заблокированного release-сценария;
- версия пакета `0.11.0`.

Подробности: `docs/MODEL_QUALIFICATION.md` и `docs/STAGE_8B_STATUS.md`.

## Результат legacy qualification

Category repository `Sandrro/text_to_function_v2` существует и содержит metadata лицензии MIT. Production approval не выдан, поскольку не зафиксированы immutable revision и SHA-256 выбранных весов, а происхождение обучающих данных не раскрыто в достаточном для проверки объёме.

Запрошенный topic repository `Sandrro/text_to_subfunction_v10` не подтверждён в публичном перечне моделей автора. Другие модели автора не подставляются автоматически вместо него.

Immutable revision токенизатора `cointegrated/rubert-tiny2` зафиксирована как `e8ed3b0c8bbf4fb6984c3de043bf7d2f4e5969ae`.

Текущий qualification report должен возвращать `approved_for_production=false`. Дополнительно отсутствуют утверждённый ручной validation set, реальные CPU/GPU benchmarks, quality report, calibration evidence и drift baseline.

Блокировка является ожидаемым безопасным результатом. Production registry нельзя активировать до получения отчёта, в котором каждый обязательный gate имеет состояние `passed`.
