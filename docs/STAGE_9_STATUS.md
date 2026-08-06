# Этап 9: геолокация и OSM-контур

Статус: завершён 6 августа 2026 года.

Geo Analyzer 2 не изменялся.

## Результат

Этап выполнен двумя подэтапами. В 9A создан новый production-контур `soika_uds.geolocation`, не зависящий от legacy-класса `factfinder.Geocoder`. В 9B контур прошёл fail-closed qualification на зафиксированном target-city validation set и получил production registry для подтверждённой области применения.

## Реализованный контур

NER, нормализация, поиск кандидатов, семантическое ранжирование, OSM-провайдеры и orchestration разделены. Natasha создаётся лениво и работает через Python 3.11 compatibility bridge к `pymorphy3`. Модельный пакет, dependency graph, validation set, runtime config, qualification report и registry закреплены версиями и SHA-256.

Nominatim и Overpass вызываются только по HTTPS. Транспорт поддерживает timeout, retry/backoff, rate limiting, идентифицируемый User-Agent и persistent SQLite cache. Некорректные ответы не попадают в кеш, malformed JSON считается permanent provider error.

Кандидаты сохраняются вместе с confidence, OSM ID, адресными атрибутами, причинами ранжирования, GeoJSON и provenance. Расстояния рассчитываются в метрах через локальную UTM CRS. Результаты и конфигурация имеют детерминированные digests.

## Qualification

Validation set `soika-geolocation-ru-target-cities` версии `1.0.0` содержит 24 вручную фиксированных случая: по восемь для Москвы, Санкт-Петербурга и Казани. Его канонический digest:

`67a9573b285f0a8343f9e966fd1951b2fc1a9a3c5f36d8f72aae140b8d791685`

Финальный live benchmark подтвердил:

- extraction exact rate: 0,958333;
- resolution rate: 0,958333;
- within-tolerance rate: 0,875;
- kind accuracy: 0,958333;
- low-confidence rate: 0;
- median distance: 119,782 м;
- p95 distance: 1 352,251 м.

Все обязательные gates прошли: model audit, model smoke, validation manifest, sample size, extraction, confidence, resolution, distance, kind, per-city quality, pinned runtime config и provider policy.

Report digest: `0d68d8c102da8548703b0468e64f429cd9c9a8d0a210aa40674a55c441bbfd73`.

Registry digest: `6f3be8ddd720bce2b29183a44640dacccac24fd3a9146ec7c3c2c9605b586da9`.

Prediction digest: `141228c900845bc300ebfff79705cc06caf2adb83aeec05ccd5b0f39348d6bf2`.

## Production scope

Production approval распространяется только на уровни `house`, `poi` и `landmark`.

Технически поддерживаемые уровни `street`, `intersection`, `district`, `city` и `unknown` не считаются подтверждёнными этим validation set. `QualifiedGeolocationEngine` помечает их причиной `geolocation_level_not_qualified` и не включает в дальнейший анализ.

Production registry фиксирует `semantic-v1`, `min_confidence=0.25`, `max_candidates=5`, язык `ru`, country code `ru`, разрешённые уровни и digests qualification evidence. Registry загружается fail-closed, рекурсивно замораживается и отклоняется при любом изменении digest.

Публичный Nominatim разрешён только для контролируемого qualification workflow с ограничением один запрос в секунду и persistent cache. Production factory требует отдельный HTTPS endpoint и явно запрещает `nominatim.openstreetmap.org`.

## Проверки

Контур покрыт unit-тестами контрактов, semantic ranking, model bridge, deep immutability, tamper protection, production scope и provider policy. CI проверяет Python compilation, Ruff, полный unit suite, `poetry.lock`, CPU Docker, health/readiness и GPU target build. Отдельный workflow повторно выполняет live qualification и сохраняет predictions, report и registry.

Qualification evidence зафиксировано в `evidence/geolocation/v1`.

## Критерий завершения

Критерий этапа выполнен: адресный benchmark подтверждает точность на целевых городах, область production approval ограничена проверенными уровнями, а исполняемый runtime активируется только через проверенный registry.

Следующий активный этап: этап 10 — пространственная фильтрация территории.
