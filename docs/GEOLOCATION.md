# Геолокация и OSM-контур

Production-реализация этапа 9 находится в пакете `soika_uds.geolocation`. Legacy-класс `factfinder.src.geocoder.Geocoder` сохранён только для обратной совместимости и не используется как production-контур.

## Архитектура

Процесс разделён на независимые слои:

1. `MentionExtractor` выделяет адресное упоминание.
2. `AddressNormalizer` нормализует текст, морфологию и тип объекта.
3. `CandidateProvider` получает альтернативные геокандидаты.
4. `GeolocationEngine` ранжирует кандидатов, вычисляет confidence и формирует воспроизводимый результат.
5. `GeolocationStageHandler` связывает результат с `PipelineStage.GEOLOCATION`.
6. `evaluate_geolocation` измеряет точность на утверждённом адресном benchmark.

Поддерживаются уровни `house`, `street`, `intersection`, `poi`, `district`, `landmark`, `city` и `unknown`.

## NER и model manager

`LocalFlairAddressExtractor` принимает только абсолютный локальный путь, полный 40-символьный commit SHA и SHA-256 артефакта. Перед первой загрузкой выполняется обязательный `artifact_verifier`. Встроенная реализация `verify_model_artifact` проверяет SHA-256 файла либо детерминированный digest дерева каталога.

Тяжёлые модели загружаются через `LazyModelManager` только при первом обращении. Natasha также создаётся лениво: module-level `Segmenter`, `NewsEmbedding`, taggers и extractors отсутствуют.

Порядок fallback задаётся явно через `CompositeMentionExtractor`, например:

```python
extractor = CompositeMentionExtractor(
    (
        local_flair_extractor,
        NatashaAddressExtractor(manager),
        RuleBasedMentionExtractor(),
    )
)
```

## Нормализация

`AddressNormalizer` использует Unicode NFKC, удаляет служебные скобки, нормализует пробелы и при необходимости применяет лениво созданный `pymorphy3.MorphAnalyzer`.

`is_missing` корректно распознаёт `None`, `NaN` и пустые строки. Проверка вида `value == np.nan` не используется.

Нормализация выделяет номер дома и корпус, две улицы перекрёстка, POI, район и ориентир. Исходное упоминание сохраняется рядом с нормализованным представлением.

## Nominatim

`NominatimClient` использует Search API с `format=jsonv2`, `addressdetails=1`, языком, ограничением страны, слоями и лимитом альтернатив. Все ответы проходят через persistent cache.

Для публичного сервиса следует использовать `public_nominatim_client`. Этот конструктор применяет описательный User-Agent и `RateLimiter(1.0)`, то есть не более одного запроса в секунду. Для production-нагрузки рекомендуется собственный или договорной Nominatim endpoint; адрес сервиса задаётся конфигурацией и не зашит в аналитическое ядро.

## Overpass

`OverpassClient` принимает только HTTPS endpoint. Каждый запрос содержит явный `[timeout:N]`, проходит через общий retrying transport и persistent cache. Имя OSM-объекта экранируется как регулярное выражение и как строка Overpass QL.

Overpass используется как дополнительный nearby-поиск для POI, ориентиров и районов после получения начальной точки. Радиус ограничен 50 км, число результатов — 100.

## Сетевой transport

`RequestsJsonTransport` обеспечивает:

- обязательный описательный User-Agent;
- HTTPS-only внешние OSM endpoints;
- connect/read timeout через общий timeout параметр;
- ограниченное число повторов;
- exponential backoff;
- повтор только для временных HTTP-статусов и сетевых ошибок;
- внедряемый rate limiter;
- структурированную ошибку с признаком retryable.

`GeolocationStageHandler` преобразует временные ошибки провайдера в `RetryableStageError`, а malformed response и нарушение контракта — в `PermanentStageError`.

## Persistent cache

`SQLiteResponseCache` хранит JSON-ответы в SQLite с WAL, namespace, детерминированным SHA-256 ключом и TTL. Cache key включает операцию и все параметры запроса. Истёкшие записи удаляются при чтении.

Кеш обязателен для публичного Nominatim, снижает нагрузку на Overpass и обеспечивает повторяемость краткосрочных повторных запусков. Директория кеша должна находиться на persistent volume.

## CRS и расстояния

Внешняя геометрия хранится в WGS84 (`EPSG:4326`). Для расстояний выбирается локальная WGS84 UTM-зона через `metric_crs_for`; преобразование выполняется `pyproj.Transformer` с `always_xy=True`.

Расстояния и последующее буферирование не должны выполняться непосредственно в градусах. Вне диапазона применимости UTM используется явный fallback `EPSG:3857`, который должен рассматриваться как пониженная точность.

Каждый выбранный результат содержит `metric_crs`. Альтернативные кандидаты остаются в WGS84 и могут быть повторно ранжированы на следующих этапах.

## Результат

Для каждого сообщения сохраняются:

- исходное и нормализованное упоминание;
- тип объекта и источник NER;
- все альтернативные кандидаты в установленном лимите;
- выбранный candidate ID;
- GeoJSON Point;
- confidence упоминания, кандидата и итоговый confidence;
- OSM type/ID и address tags;
- причина исключения;
- метрическая CRS;
- provenance extractor/provider/config;
- input, config и output digests.

Порядок входных сообщений и порядок одинаковых кандидатов не влияют на результат.

## Адресный benchmark

`evaluate_geolocation` принимает утверждённые случаи с `message_key`, городом, ожидаемой точкой, ожидаемым уровнем и допустимым расстоянием в метрах. Отчёт содержит:

- resolution rate;
- within-tolerance rate;
- kind accuracy;
- median и p95 расстояния;
- показатели отдельно по каждому городу;
- детерминированный report digest.

Синтетические unit fixtures подтверждают только корректность алгоритма метрик. Production-допуск требует отдельного ручного validation set для целевых городов, зафиксированной версии набора и сохранённого benchmark report.

## Ограничения внешних сервисов

Использование публичных OSM-сервисов должно соответствовать их актуальным политикам. Для публичного Nominatim обязательны caching, идентифицируемый User-Agent и ограничение частоты. Массовое геокодирование следует переносить на собственную или договорную инфраструктуру.

OpenStreetMap attribution и условия ODbL должны быть сохранены в пользовательском интерфейсе и экспортируемом результате продукта.
