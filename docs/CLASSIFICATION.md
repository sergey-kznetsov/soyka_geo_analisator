# Классификация и уточнение тем

Стадия классификации выполняется после `PipelineStage.PREPROCESSING` и до геолокации. Она принимает только JSON-compatible сообщения preprocessing и не изменяет исходный текст.

## Поток

1. Из preprocessing выбираются принятые сообщения, включённые в анализ.
2. `ClassificationRegistry` проверяет наличие моделей `category` и `topic`.
3. Для модели и токенизатора обязательны immutable revision.
4. Модель должна быть явно помечена `approved_for_production=true`.
5. `PredictionBackend` выполняет пакетный inference.
6. Scores проходят через зафиксированный калибратор.
7. Пороговые правила формируют `low_confidence` и причины.
8. Результат содержит model, tokenizer, registry, config и calibration provenance.
9. `ClassificationStageHandler` сохраняет JSON-only output стадии `NLP`.

## Fail-closed model registry

Production registry не загружается, если:

- отсутствует category или topic model;
- присутствует неизвестная роль;
- revision равна `main` или `master`;
- revision не является immutable идентификатором;
- лицензия или обучающие данные не прошли review;
- `approved_for_production=false`.

Модель не становится разрешённой автоматически после технической загрузки. Одобрение требует отдельного документированного решения по лицензии, происхождению данных, ограничениям и результатам валидации.

## Confidence

Конфигурация содержит независимые пороги категории и темы. Низкая уверенность всегда сохраняется в результате и не заменяется наиболее вероятной меткой без предупреждения.

Калибровка поддерживает:

- identity calibrator для тестов и неподтверждённых экспериментальных запусков;
- piecewise-linear curve, построенную на утверждённом validation set;
- обязательный digest validation set в provenance.

Коэффициенты нельзя выбирать по производственным данным задним числом.

## Quality validation

`evaluate_predictions` рассчитывает:

- category accuracy;
- category macro-F1;
- topic accuracy;
- topic macro-F1;
- долю low-confidence;
- digest отчёта.

Drift отслеживается сравнением распределений меток через total variation distance. Порог алерта должен утверждаться после получения базового производственного распределения.

## CPU/GPU

`TransformersPredictionBackend` использует одинаковые model/tokenizer revisions и один контракт результата. CPU соответствует `device=-1`, GPU — `device=0`. Выбор устройства входит в provenance.

## Ограничения legacy-моделей

`Sandrro/text_to_function_v2` имеет MIT metadata, но model card не описывает обучающий набор. Для topic-модели в текущем manifest указана неизвестная лицензия и требуется проверка существования репозитория, immutable revision и обучающих данных. До закрытия этих вопросов legacy-модели не должны получать production approval.

Техническая платформа этапа не заменяет оценку качества и юридический аудит конкретных весов модели.
