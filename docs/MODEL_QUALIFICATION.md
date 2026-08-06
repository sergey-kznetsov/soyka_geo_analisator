# Квалификация моделей классификации

Этап 8B не включает автоматическое одобрение найденных весов. Его задача — сделать решение о production-допуске воспроизводимым, проверяемым и fail-closed.

Geo Analyzer 2 не изменяется. Квалификация выполняется внутри серверного модуля СОЙКИ.

## Входной документ

Машиночитаемый qualification document содержит:

- audit category- и topic-моделей;
- существование официального репозитория;
- immutable commit SHA модели и токенизатора;
- идентификатор и результат проверки лицензии;
- сведения о происхождении обучающих данных;
- документированное назначение и ограничения;
- формат весов и SHA-256;
- manifest утверждённого validation set;
- CPU- и GPU-benchmark evidence;
- quality, calibration и drift evidence;
- утверждённую qualification policy.

Неизвестные поля и элементы неправильного типа отклоняются. Отсутствующие доказательства формируют blocker, а не неявное разрешение.

## Release gates

`qualify_release` проверяет:

1. Наличие обеих ролей: `category` и `topic`.
2. Подтверждённый официальный репозиторий.
3. Immutable 40-символьный commit SHA модели.
4. Проверенную лицензию.
5. Документированное и проверенное происхождение обучающих данных.
6. Документированные назначение и ограничения модели.
7. Разрешённый формат весов, по умолчанию `safetensors`.
8. SHA-256 файла весов.
9. Immutable revision токенизатора.
10. Источники доказательств.
11. Утверждение, размер, покрытие и согласованность разметки validation set.
12. Реальный CPU benchmark.
13. Реальный GPU benchmark, когда он обязателен политикой.
14. Эквивалентность детерминированного результата CPU и GPU.
15. Macro-F1 и macro-recall категории и темы.
16. Долю low-confidence результатов.
17. Expected Calibration Error категории и темы.
18. Наличие calibration digest.
19. Допустимый total-variation drift.
20. Наличие digest базового распределения.

Production approval устанавливается только тогда, когда каждый обязательный gate имеет состояние `passed`.

## Метрики

`evaluate_predictions` формирует:

- accuracy;
- macro-precision;
- macro-recall;
- macro-F1;
- precision, recall, F1 и support по каждой метке;
- confusion matrix;
- low-confidence rate;
- Expected Calibration Error;
- Brier score;
- статистику calibration bins;
- детерминированный digest отчёта.

Повторяющиеся `message_key` в validation или prediction input отклоняются, чтобы одна запись не могла незаметно повлиять на метрики несколько раз.

## Запуск

Проверка выполняется командой:

```bash
python -m soika_uds.classification.qualification_cli \
  --input configs/classification/stage8b-legacy-qualification.json \
  --output qualification-report.json \
  --strict
```

Код возврата:

- `0` — отчёт сформирован, а при отсутствии `--strict` blockers не влияют на код процесса;
- `2` — включён `--strict` и хотя бы один release gate заблокирован.

Отчёт содержит `input_digest`, `report_digest`, список всех gates и массив blocker codes.

## Текущий legacy audit

Файл `configs/classification/stage8b-legacy-qualification.json` фиксирует состояние на 6 августа 2026 года.

### Category model

`Sandrro/text_to_function_v2` присутствует на официальном Hugging Face и имеет metadata лицензии MIT. Карточка модели не раскрывает обучающий и проверочный набор в объёме, достаточном для production review. Immutable revision конкретных весов и SHA-256 в audit пока не зафиксированы.

### Topic model

Запрошенный legacy-идентификатор `Sandrro/text_to_subfunction_v10` не найден в публичном перечне моделей автора. В перечне присутствуют другие модели, включая `text_to_subfunction_v7` и `text_to_topic`, но они не заменяются автоматически: это другие артефакты, требующие отдельного аудита и сравнения label space.

### Tokenizer

Для `cointegrated/rubert-tiny2` зафиксирована immutable revision `e8ed3b0c8bbf4fb6984c3de043bf7d2f4e5969ae`. Это не закрывает release gates самих классификационных весов.

## Текущий результат

Legacy qualification остаётся `approved_for_production=false`. Основные blockers:

- отсутствует immutable revision category weights;
- не проверено происхождение обучающих данных category model;
- topic repository v10 не подтверждён;
- topic license и обучающие данные не подтверждены;
- отсутствует утверждённый ручной validation set;
- отсутствуют реальные CPU/GPU benchmarks;
- отсутствует quality report;
- отсутствуют calibration curve и drift baseline.

Блокировка является ожидаемым безопасным результатом этапа 8B. Она не должна сниматься фиктивными digest, синтетическими benchmark-значениями или подменой модели.
