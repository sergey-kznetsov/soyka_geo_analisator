# Квалификация моделей классификации

Qualification является fail-closed решением о допуске конкретных весов. Она не активирует найденную модель автоматически и не допускает подмену evidence от другой модели или другого validation set.

## Schema 2

Входной документ содержит:

- audit category- и topic-моделей;
- immutable model/tokenizer commit SHA;
- SHA-256 весов;
- license и training-data review;
- intended use и ограничения;
- полный category/topic label space;
- label map;
- полный category-to-topic hierarchy;
- утверждённый validation set;
- CPU/GPU benchmark evidence;
- quality, calibration и drift evidence;
- qualification policy.

Неизвестные поля, malformed элементы и boolean в числовых полях отклоняются.

## Канонический model registry digest

`audited_model_registry_digest` формируется из идентичности фактических артефактов и таксономии:

- role;
- repository ID;
- resolved model revision;
- weights SHA-256;
- tokenizer ID и revision;
- task;
- label space;
- label map;
- topic hierarchy.

CPU и GPU benchmark обязаны содержать этот digest. Quality evidence также обязана содержать его. Равенство digest только между двумя benchmarks недостаточно.

## Validation binding

Validation manifest содержит собственный SHA-256. Benchmark и quality evidence обязаны ссылаться на этот digest.

Проверяются:

- утверждение набора;
- минимальный общий размер;
- точное совпадение category labels с category model label space;
- точное совпадение topic labels с topic model label space;
- минимальное число примеров по каждой метке;
- число аннотаторов на запись;
- согласованность разметки.

Набор из одной категории или неполного списка тем не может пройти qualification, даже если его macro-F1 высок.

## Benchmark gates

Для каждого обязательного устройства проверяются:

- наличие и завершение benchmark;
- число повторов;
- совпадение validation digest;
- совпадение числа записей validation set;
- совпадение model registry digest;
- детерминированный output digest.

При обязательном GPU CPU и GPU должны иметь одинаковые output и model registry digests.

## Quality gates

Quality evidence связана одновременно с model registry digest и validation digest. Дополнительно проверяются:

- sample count;
- category macro-F1 и macro-recall;
- topic macro-F1 и macro-recall;
- low-confidence rate;
- category/topic ECE;
- calibration digest;
- total-variation drift;
- baseline digest.

`report_digest` quality evidence не заменяет связь с моделями и validation set.

## Qualification report

Отчёт содержит:

- `approved_for_production`;
- полный список gates;
- blocker codes;
- input digest;
- model registry digest;
- validation digest;
- report digest.

`report_digest` рассчитывается по approval, gates и всем связывающим digest. Production registry loader пересчитывает его перед загрузкой.

## Production registry

Ручного `approved_for_production=true` в model descriptor недостаточно. Loader требует полный успешный qualification report и проверяет совпадение model registry digest с runtime registry.

## Запуск

```bash
soika-classification-qualify \
  --input configs/classification/stage8b-legacy-qualification.json \
  --output qualification-report.json \
  --strict
```

При blockers strict-режим возвращает код 2.

## Legacy audit

Текущий legacy manifest использует schema 2, но намеренно не содержит неизвестные доказательства. Для category/topic моделей отсутствуют полный label space, topic hierarchy, immutable model revisions, weights digests, утверждённый validation set, реальные benchmarks и quality evidence.

Поэтому ожидаемый результат остаётся `approved_for_production=false`. Заполнять эти поля фиктивными SHA-256 или синтетическими метриками запрещено.
