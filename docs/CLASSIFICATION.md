# Классификация и уточнение тем

Стадия классификации выполняется после `PipelineStage.PREPROCESSING` и до геолокации. Она принимает JSON-compatible результат preprocessing, использует `model_text` и не изменяет исходный текст.

## Поток

1. Выбираются принятые сообщения, включённые в анализ.
2. Production registry проверяет category/topic models и успешный qualification report.
3. Backend выполняет пакетный category inference.
4. Category score калибруется.
5. Для предсказанной категории registry возвращает разрешённый набор уточнённых тем.
6. Topic inference фильтруется по этому набору до выбора результата.
7. Topic score калибруется.
8. Независимые пороги формируют low-confidence и причины.
9. Общий confidence band рассчитывается по обоим результатам.
10. Результат сохраняет model, tokenizer, weights, registry, qualification, config, device и calibration provenance.
11. `ClassificationStageHandler` сохраняет JSON-only output стадии `NLP`.

## Production registry schema 2

Для каждой модели обязательны:

- роль и совпадающий `task`;
- repository ID;
- полный 40-символьный model commit SHA;
- tokenizer ID и полный tokenizer commit SHA;
- SHA-256 весов;
- проверенная лицензия и training-data review;
- явный production approval;
- полный label space;
- необязательный label map, все значения которого входят в label space.

Registry также содержит полный `topic_hierarchy`. Набор ключей hierarchy обязан точно совпадать с category label space, а объединение тем — с topic label space.

Registry загружается только вместе с qualification report. Report должен иметь `approved_for_production=true`, пустой список blockers, корректный `report_digest` и `model_registry_digest`, совпадающий с runtime registry.

## Immutable revisions

Строковые ветки, теги и сокращённые SHA запрещены. Значения `main`, `master`, `release-v1`, `latest` и аналогичные не проходят контракт. Допускается только lowercase hexadecimal commit SHA длиной 40 символов.

## Model registry digest

Канонический digest включает:

- role;
- repo ID;
- model revision;
- weights SHA-256;
- tokenizer ID и revision;
- task;
- label space;
- label map;
- topic hierarchy.

Этот же digest обязан присутствовать в CPU/GPU benchmark и quality evidence.

## Confidence

Category и topic имеют отдельные пороги. Результат считается low-confidence, если хотя бы один score ниже своего порога.

Общий band:

- `low` — хотя бы один score ниже соответствующего порога;
- `high` — оба score не ниже high-confidence threshold;
- `medium` — остальные допустимые случаи.

Поэтому результат не может одновременно иметь `confidence_band=high` и `low_confidence=true`.

## Calibration

Поддерживаются identity calibrator и monotonic piecewise-linear curve. Production curve должна иметь digest утверждённого validation set. Коэффициенты нельзя подбирать по production data задним числом.

## Backend

`TransformersPredictionBackend`:

- использует одинаковый контракт CPU/GPU;
- по умолчанию передаёт `local_files_only=true`;
- поддерживает artifact verifier до создания pipeline;
- кеширует pipeline по model repo, model revision, weights digest, tokenizer ID, tokenizer revision и device.

Artifact verifier должен сопоставить descriptor с установленным и проверенным локальным артефактом.

## Provenance

Provenance содержит:

- runtime registry digest;
- model registry digest;
- qualification report digest;
- config digest;
- device;
- полные category/topic descriptors;
- разрешённые темы выбранной категории;
- calibration descriptors.

Вся вложенная структура рекурсивно неизменяема. Сериализация не изменяет рассчитанный output digest.

## Quality validation

`evaluate_predictions` рассчитывает accuracy, macro-precision, macro-recall, macro-F1, per-label metrics, confusion matrices, ECE, Brier score, calibration bins и low-confidence rate.

Qualification дополнительно требует точного совпадения полного label space validation set с квалифицируемыми моделями, минимального числа примеров по каждой метке, утверждения набора, глубины разметки и согласованности аннотаторов.

## Legacy models

Текущие legacy category/topic models не имеют полного набора доказательств и не должны загружаться в production registry. Техническая доступность репозитория или ручной флаг approval не заменяют qualification.
