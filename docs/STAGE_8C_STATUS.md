# Этап 8C: финальное усиление классификации

Статус: реализован.

Этап 8C закрывает замечания аудита и review к PR №12 и №13. Его цель — исключить ложный production approval и обеспечить непрерывную связь между квалифицируемыми весами, validation set, benchmark, quality report, production registry и runtime provenance.

## Инварианты runtime

1. Model и tokenizer revisions имеют формат полного 40-символьного commit SHA.
2. Модель может занимать только совпадающую роль: `category` или `topic`.
3. Model descriptor содержит SHA-256 весов и полный label space.
4. Topic hierarchy полностью покрывает category и topic label spaces.
5. Topic prediction фильтруется по категории до выбора top-1.
6. Общий confidence band учитывает оба результата.
7. Provenance рекурсивно неизменяем и входит в output digest.
8. Pipeline cache key включает identity модели, весов и токенизатора.
9. Transformers backend по умолчанию работает без сетевого fallback.
10. Artifact verifier выполняется до создания pipeline.

## Инварианты qualification

Канонический `model_registry_digest` строится из:

- роли модели;
- repository ID;
- immutable model revision;
- SHA-256 весов;
- tokenizer ID и immutable revision;
- полного label space;
- label map;
- category-to-topic hierarchy.

CPU и GPU benchmark обязаны содержать этот digest и digest утверждённого validation set. Quality evidence обязана содержать оба digest. Совпадения только по размеру выборки недостаточно.

Validation set проходит gate только при точном совпадении category/topic label sets с квалифицируемыми моделями. После этого проверяются минимальное число примеров по каждой метке, глубина разметки и согласованность аннотаторов.

## Production registry

Production registry schema 2 загружается только вместе с полным qualification report. Loader проверяет:

- `approved_for_production=true`;
- отсутствие blockers;
- SHA-256 форматы;
- соответствие `report_digest` фактическому содержимому отчёта;
- совпадение model registry digest отчёта и runtime registry;
- совпадение роли и task каждой модели;
- полноту topic hierarchy.

Ручной флаг `approved_for_production=true` без связанного успешного qualification report не является достаточным основанием для загрузки registry.

## Тесты

Добавлены регрессии для:

- mutable revisions;
- перепутанных model roles;
- tokenizer cache collision;
- противоречивого confidence;
- изменяемого nested provenance;
- benchmark от другой версии моделей;
- quality report от другой модели или validation set;
- неполного label space;
- неполной topic hierarchy;
- boolean вместо числовых метрик;
- artifact verifier и local-only backend.

## Ограничение

Этап 8C завершает безопасную техническую реализацию. Он не создаёт фиктивные доказательства для legacy-моделей. До появления реальных весов, юридического и training-data review, утверждённой таксономии, validation set и CPU/GPU evidence production activation остаётся заблокированной.
