# Этап 8: классификация и уточнение тем

Статус: техническая платформа реализована; production model approval и quality benchmark остаются открытыми release-gates.

Geo Analyzer 2 не изменялся.

## Реализовано

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
- accuracy, macro-F1 и low-confidence metrics;
- label distribution и total-variation drift;
- unit tests без сети и загрузки моделей;
- версия пакета `0.10.0`.

## Открытые release-gates

1. Получить immutable commit SHA legacy category/topic models.
2. Подтвердить лицензию topic model.
3. Получить описание происхождения и состава обучающих данных обеих моделей.
4. Подготовить утверждённый ручной validation set по целевым городским категориям.
5. Выполнить реальный CPU/GPU inference на закреплённых весах.
6. Рассчитать category/topic precision, recall, macro-F1 и confusion matrices.
7. Построить и зафиксировать confidence calibration curve.
8. Утвердить low-confidence и drift thresholds.

До закрытия этих пунктов модели должны оставаться `approved_for_production=false`. Техническая платформа может быть объединена, но этап нельзя считать полностью завершённым по критерию качества.
