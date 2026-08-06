# Этап 8: классификация и уточнение тем

Статус: технический контур этапа 8 завершён в редакции 8C 6 августа 2026 года. Production activation конкретных моделей остаётся fail-closed до получения реального qualification report со всеми пройденными gates.

Geo Analyzer 2 не изменялся.

## 8A: production-платформа

Реализованы неизменяемые контракты классификации, пакетный inference, CPU/GPU backend, независимые category/topic thresholds, калибровка, low-confidence, provenance, детерминированный output digest и handler `PipelineStage.NLP`.

## 8B: qualification и release gates

Реализованы model audit, validation-set evidence, CPU/GPU benchmark evidence, quality/calibration/drift metrics, строгий JSON loader, qualification CLI и машиночитаемый legacy audit.

## 8C: финальное усиление

Закрыты дефекты воспроизводимости и ложного production approval:

- model и tokenizer revisions принимаются только как 40-символьные commit SHA;
- роль registry обязана совпадать с `ModelDescriptor.task`;
- cache key учитывает модель, веса, токенизатор, revisions и устройство;
- provenance рекурсивно неизменяем;
- общий confidence учитывает и категорию, и тему;
- тема выбирается только внутри разрешённого набора для предсказанной категории;
- полный category/topic label space обязателен;
- validation set должен точно покрывать label space квалифицируемых моделей;
- CPU/GPU benchmarks связаны с каноническим model registry digest;
- quality report связан одновременно с model registry digest и validation digest;
- boolean-значения отклоняются в числовых полях qualification;
- production registry загружается только вместе с успешным qualification report;
- содержимое qualification report проверяется по собственному digest;
- backend по умолчанию использует `local_files_only=true` и поддерживает обязательный artifact verifier;
- версия пакета повышена до `0.12.0`.

Подробности: `docs/CLASSIFICATION.md`, `docs/MODEL_QUALIFICATION.md` и `docs/STAGE_8C_STATUS.md`.

## Текущие legacy-модели

`Sandrro/text_to_function_v2` и `Sandrro/text_to_subfunction_v10` не активированы. Для них отсутствует полный набор обязательных доказательств: immutable revision и digest весов, проверенное происхождение обучающих данных, утверждённый label space и taxonomy, ручной validation set, реальные CPU/GPU benchmarks, quality report, calibration evidence и drift baseline.

Текущий audit обязан возвращать `approved_for_production=false`. Это ожидаемый безопасный результат.

## Критерий завершения

Технический критерий этапа 8 выполнен: платформа не допускает модели, benchmark или quality evidence, не связанные с одним каноническим model registry digest.

Продуктовый допуск конкретных весов считается выполненным только для отчёта, где `approved_for_production=true`, отсутствуют blockers, report digest корректен, а production registry соответствует model registry digest этого отчёта.
