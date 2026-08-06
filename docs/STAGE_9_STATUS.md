# Этап 9: геолокация и OSM-контур

Статус: техническая production-платформа реализована. Фактический production-допуск конкретной NER-модели и точности геокодирования остаётся заблокированным до утверждённого адресного benchmark по целевым городам.

Geo Analyzer 2 не изменялся.

## Реализовано

- отдельный пакет `soika_uds.geolocation`;
- разделение NER, нормализации, поиска кандидатов, OSM providers, ranking и orchestration;
- immutable JSON-compatible контракты;
- ленивый thread-safe model manager;
- local-only Flair NER с immutable revision и SHA-256;
- обязательная проверка локального модельного артефакта;
- ленивый Natasha fallback без module-level моделей;
- `pymorphy3` для морфологической нормализации;
- корректная обработка `None`, `NaN` и пустых строк;
- поддержка дома, улицы, перекрёстка, POI, района, ориентира и города;
- Nominatim Search API adapter;
- policy-compliant factory публичного Nominatim с лимитом 1 запрос/сек;
- HTTPS-only Overpass adapter с query timeout;
- общий HTTP timeout, retry, exponential backoff и rate limit;
- SQLite persistent cache с WAL и TTL;
- альтернативные кандидаты, выбранный candidate ID и confidence;
- OSM type/ID, address tags и причины ранжирования;
- локальная UTM CRS для расстояний;
- stage handler `PipelineStage.GEOLOCATION`;
- retryable/permanent классификация внешних ошибок;
- input/config/output digests и provenance;
- адресные benchmark metrics по городам;
- deterministic unit tests без сети и моделей.

Подробности: `docs/GEOLOCATION.md`.

## Исправленные legacy-дефекты

Production-контур не использует `value == np.nan`, глобальную Natasha-инициализацию, HTTP Overpass, неограниченные сетевые вызовы, in-memory-only cache или вычисление расстояний непосредственно в EPSG:4326.

Legacy-класс `factfinder.src.geocoder.Geocoder` не переписывался и остаётся только совместимым исследовательским API. Новая orchestration stage использует исключительно `soika_uds.geolocation`.

## Открытый release gate

Технические тесты не являются доказательством адресной точности. До production activation необходимо:

1. Утвердить целевые города и уровни объектов.
2. Подготовить ручной validation set с ожидаемыми координатами и допустимыми расстояниями.
3. Зафиксировать версию и digest validation set.
4. Зафиксировать локальный NER artifact, commit SHA и SHA-256.
5. Выполнить benchmark на серверном окружении.
6. Утвердить пороги resolution rate, within-tolerance rate, kind accuracy, median и p95 distance.
7. Сохранить воспроизводимый report digest.

До закрытия этих условий геолокация должна использоваться в режиме технической интеграции и сохранять low-confidence/unresolved результаты, но не объявляться подтверждённой по качеству.

## Критерий завершения этапа

Технический критерий выполнен: контур воспроизводим, детерминирован, защищён от сетевых сбоев и использует метрическую CRS.

Продуктовый критерий из плана будет выполнен только после того, как утверждённый адресный benchmark подтвердит точность на целевых городах.
