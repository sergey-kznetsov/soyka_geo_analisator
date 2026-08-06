# Этап 8B: квалификация моделей и release gates

Статус: реализован. Текущие legacy-модели не получили production approval, поскольку обязательные доказательства и validation/benchmark результаты отсутствуют.

Geo Analyzer 2 не изменялся.

## Реализовано

- fail-closed qualification contract;
- строгий JSON loader без молчаливого пропуска malformed элементов;
- model audit для ролей `category` и `topic`;
- обязательные immutable model/tokenizer revisions;
- license и training-data review gates;
- intended-use и limitations gate;
- allowlist форматов весов;
- обязательный SHA-256 весов;
- validation-set manifest;
- контроль размера и покрытия меток;
- контроль числа аннотаторов и согласованности разметки;
- CPU/GPU benchmark evidence;
- проверка эквивалентности CPU/GPU output digest;
- macro-precision, macro-recall и macro-F1;
- per-label precision, recall, F1 и support;
- confusion matrices;
- Expected Calibration Error и Brier score;
- calibration-bin statistics;
- low-confidence gate;
- total-variation drift gate;
- calibration и baseline digests;
- детерминированные input/report digests;
- CLI для формирования qualification report;
- машиночитаемый legacy audit;
- тесты успешного и заблокированного release-сценария.

Подробности: `docs/MODEL_QUALIFICATION.md`.

## Результат проверки legacy-моделей

`Sandrro/text_to_function_v2` подтверждён как существующий публичный репозиторий с metadata лицензии MIT. Production approval заблокирован из-за отсутствия зафиксированной immutable revision конкретных весов, SHA-256 весов и достаточного описания происхождения обучающих данных.

`Sandrro/text_to_subfunction_v10` не подтверждён в публичном перечне моделей автора. Доступные `text_to_subfunction_v7` и `text_to_topic` не подставляются автоматически вместо v10.

Токенизатор `cointegrated/rubert-tiny2` зафиксирован на revision `e8ed3b0c8bbf4fb6984c3de043bf7d2f4e5969ae`, но это не снимает блокировки классификационных моделей.

## Проверка audit manifest

Текущий fail-closed результат воспроизводится командой:

```bash
python -m soika_uds.classification.qualification_cli \
  --input configs/classification/stage8b-legacy-qualification.json \
  --output qualification-report.json \
  --strict
```

Для текущего audit manifest ожидается код возврата `2` и `approved_for_production=false`.

## Открытые внешние условия production approval

Для фактического включения моделей необходимо:

1. Выбрать и юридически проверить category/topic weights.
2. Зафиксировать immutable revisions и SHA-256.
3. Подтвердить происхождение и допустимость обучающих данных.
4. Подготовить утверждённый ручной validation set.
5. Выполнить inference на целевом CPU и GPU.
6. Получить quality report выше утверждённых порогов.
7. Зафиксировать calibration curve и baseline распределения.
8. Повторно выполнить qualification CLI с `--strict`.

До выполнения всех условий production registry должен оставаться заблокированным.
