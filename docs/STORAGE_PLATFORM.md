# Универсальное хранилище экосистемы Геоанализатора

`geoanalyzer_storage` — общий инфраструктурный слой PostgreSQL/PostGIS для серверных программ экосистемы Геоанализатора. Он не зависит от `soika_uds` и не требует, чтобы все приложения использовали одну предметную модель.

## Архитектурный принцип

Универсальность относится к инфраструктурным контрактам, а не к объединению всех предметных данных в одну таблицу.

Обязательный общий слой:

- `ga_meta` — registry приложений и scoped schema migrations;
- `ga_core` — jobs, stage checkpoints и immutable artifacts;
- `ga_cache` — durable JSON cache с TTL;
- `application_id` — обязательная граница изоляции данных разных серверных программ.

Предметные данные приложения размещаются в собственной схеме `ga_<application>`. СОЙКА использует `ga_soika`. Новое приложение может использовать только общий слой или добавить собственные типизированные таблицы и PostGIS-индексы отдельным migration scope.

Такой подход сохраняет независимые migrations, типы, foreign keys и spatial indexes. Универсальная JSON-таблица для всех доменов намеренно не используется.

## Проверенная платформа

Stage 13 проверяется на PostgreSQL 18.4 и PostGIS 3.6.4. Docker image закреплён digest и используется одинаково в development compose и live GitHub Actions integration gate.

Python-доступ реализован через Psycopg 3 и `psycopg_pool`. Storage-зависимости находятся в отдельном `requirements-storage.txt` и hash-pinned. Они загружаются лениво: импорт `soika_uds` не требует подключения к PostgreSQL.

## Подключение

DSN передаётся через environment variable, по умолчанию `GEOANALYZER_DATABASE_DSN`.

Общий слой для любого приложения:

```bash
export GEOANALYZER_DATABASE_DSN='postgresql://user@db/geoanalyzer'
geoanalyzer-storage migrate --scope platform
geoanalyzer-storage check
```

Для СОЙКИ применяются общий и предметный scopes:

```bash
geoanalyzer-storage migrate --scope platform --scope soika
```

Без `--scope` команда `migrate` применяет только `platform`. Поэтому подключение нового серверного приложения не создаёт `ga_soika` и не получает скрытой зависимости от домена СОЙКИ.

Пароль не следует передавать аргументом процесса. В production используются secret manager, `PGPASSFILE` либо другой механизм передачи credentials вне command line. Для сетевого production-подключения требуется TLS и отдельные роли с минимальными правами.

`PostgresSettings.__repr__` скрывает DSN, поэтому строка подключения не попадает в диагностический `repr`.

## Development PostGIS

Локальная общая БД запускается отдельно от server container:

```bash
docker compose -f docker-compose.storage.yml up -d
```

Порт по умолчанию публикуется только на `127.0.0.1:15432`. Пароль `geoanalyzer-dev-only` является только локальным development default и не должен использоваться на общедоступном или production-сервере.

Для PostgreSQL 18 volume монтируется в `/var/lib/postgresql`, а не в старый version-specific data path.

## Scoped migrations

`MigrationRunner` работает внутри явного `scope`. Встроенные migrations размещаются как `sql/migrations/<scope>/NNNN_name.sql` и применяются по возрастанию version.

В текущем пакете существуют два независимых scopes:

- `platform` — только общий storage contract;
- `soika` — только предметная схема СОЙКИ и её PostGIS indexes.

Журнал `ga_meta.schema_migrations` имеет ключ `(scope, version)`. Версия `0001` в одном scope не конфликтует с `0001` другого приложения.

Инварианты:

- migration выполняется транзакционно;
- параллельные migration runners сериализуются через PostgreSQL advisory transaction lock;
- `lock_timeout` ограничивает зависание на конфликтующем lock;
- SHA-256 каждой применённой migration записывается вместе со scope;
- изменение уже применённой migration приводит к `MigrationChecksumError`;
- повторное применение неизменённого scope является no-op;
- runner не принимает migration из чужого scope.

`discover_migrations(scope, package=...)` позволяет другому Python-сервису хранить свои SQL migrations в собственном package с тем же layout. То есть расширение экосистемы не требует добавлять доменный SQL каждого сервиса внутрь СОЙКИ.

После публикации migration считается immutable. Изменения выполняются новой migration в том же scope.

## Application registry

`PostgresApplicationRegistry` регистрирует серверные программы в `ga_meta.applications`.

`application_id` должен быть стабильным идентификатором программы. Общие jobs, artifacts и cache всегда включают его в primary/unique key. Поэтому одинаковые `analysis_id`, idempotency key или cache key разных приложений не пересекаются.

`domain_schema` является метаданными; создание предметной схемы выполняется migration scope самого приложения.

## Jobs и checkpoints

`ga_core.jobs` хранит canonical job record и optimistic `revision`. `ga_core.stage_checkpoints` хранит состояние каждой стадии отдельно.

`PostgresJobStore` реализует тот же store protocol, который уже использует `SoikaOrchestrator`:

- `create`;
- `create_idempotent`;
- `load`;
- `save(expected_revision=...)`;
- `list_records`;
- `find_by_idempotency_key`.

Переход с file store на PostgreSQL не меняет state machine worker'ов. Concurrent update блокируется SQL-условием `revision = expected_revision`.

Полный `JobRecord` остаётся canonical recovery source. Завершённый checkpoint дополнительно записывается в `ga_core.artifacts` как immutable `stage-output` с content SHA-256. Повторное сохранение идентичного output не создаёт дубликат.

Это даёт одновременно:

- быстрое восстановление текущего состояния;
- историю изменившихся stage outputs;
- независимый от предметной схемы storage contract для будущих серверных программ.

## Immutable artifacts

`PostgresArtifactStore` хранит произвольный версионированный JSON artifact и опциональный GeoJSON geometry.

Artifact глубоко замораживает вложенные mapping/sequence значения после валидации, поэтому content digest нельзя изменить мутацией исходного Python-объекта.

Для GeoJSON сохраняются одновременно:

- исходный `geometry_json`, участвующий в content digest;
- PostGIS `geometry(Geometry, 4326)` для spatial query и GiST index.

Round-trip проверяет сохранённый content digest. Это не позволяет незаметно заменить исходный artifact геометрией, нормализованной PostGIS.

## Durable cache

`PostgresJsonCache` — общий persistent L2 cache.

Ключ состоит из:

- `application_id`;
- `namespace`;
- deterministic SHA-256 от operation и canonical parameters.

Значение сохраняется как JSONB с SHA-256 и `expires_at`. Expired entry не возвращается. Cleanup выполняется ограниченными batch-операциями.

OSM/Nominatim/Overpass adapters принимают `ResponseCache` protocol. Поэтому production может использовать `PostgresJsonCache`, а локальные тесты — прежний `SQLiteResponseCache`.

Live integration test подтверждает критерий повторного запуска: два одинаковых Nominatim lookup выполняют HTTP transport только один раз; второй ответ читается из PostgreSQL-cache. Таким образом неизменившийся OSM lookup не скачивается повторно.

Redis не является обязательной зависимостью. Если позже понадобится L1 cache с меньшей latency, Redis можно поставить перед PostgreSQL, сохранив тот же deterministic cache contract и PostgreSQL как durable L2/source of truth.

## СОЙКА: предметная схема

`soika` migration scope создаёт `ga_soika` с типизированными таблицами для предметных запросов:

- `model_versions`;
- `source_messages`;
- `preprocessed_messages`;
- `classifications`;
- `geocoding_results`;
- `geocoding_candidates`;
- `events`;
- `event_members`;
- `event_connections`;
- `risk_history`.

Canonical stage outputs уже полностью сохраняются в `ga_core.stage_checkpoints` и immutable `ga_core.artifacts`. `ga_soika` является оптимизированной предметной проекцией для SQL/API/аналитических запросов и может эволюционировать отдельными migrations без изменения общего storage API.

Spatial columns используют SRID 4326. Для точных geocoding points созданы partial GiST indexes по geometry и geography, согласованные с eligibility contract этапа 10. Events и connection geometries также имеют GiST indexes.

## Retention

`RetentionPolicy` задаёт явные сроки, а `RetentionManager` удаляет данные bounded batches.

Default technical policy:

- completed/completed-with-warnings jobs — 90 дней;
- failed jobs — 30 дней;
- cancelled jobs — 30 дней;
- cleanup batch — 5000 записей.

Удаление job каскадно удаляет его checkpoints, artifacts и предметные rows, связанные foreign keys. Cache имеет независимый TTL и очистку.

Эти значения являются техническими defaults, а не юридическим сроком хранения. Конкретный deployment обязан настроить retention согласно своему data-protection policy и требованиям заказчика.

## Backup и restore

`BackupTarget` и command builders формируют безопасный baseline:

- backup — `pg_dump --format=custom --no-owner --no-acl`;
- restore — `pg_restore --clean --if-exists --no-owner --no-acl --exit-on-error`.

Команды возвращаются как argv, а не shell-строка; host допускает стандартные PostgreSQL endpoint формы, включая IPv6. Пароль намеренно отсутствует в command builder. Backup job должен получать credentials через secret infrastructure/`PGPASSFILE`.

Production strategy:

1. регулярный logical custom-format backup;
2. encrypted storage вне основного PostgreSQL host;
3. ежедневная проверка наличия и размера backup;
4. периодический restore в отдельную test database;
5. проверка `geoanalyzer-storage check`, migrations и ключевых row counts после restore;
6. отдельный retention policy для backup generations.

Для крупных installations logical backup может быть дополнен physical/WAL/PITR средствами PostgreSQL-платформы. Это deployment concern и не вшивается в application package.

## Подключение нового серверного приложения

Минимальный путь:

1. выбрать стабильный lowercase `application_id`;
2. применить `platform` scope;
3. зарегистрировать приложение через `PostgresApplicationRegistry`;
4. использовать `PostgresJsonCache` для durable cache;
5. использовать `PostgresArtifactStore` для immutable outputs;
6. при наличии pipeline реализовать/использовать совместимый job-store contract;
7. при необходимости добавить собственную `ga_<application>` schema в отдельном migration package/scope;
8. добавить live integration tests на PostgreSQL/PostGIS для новых spatial/index invariants.

Не следует менять общий `search_path` между приложениями. SQL общего слоя использует fully-qualified schema names, что уменьшает риск случайного обращения к объектам другого сервиса.

## Граница stage 13

Stage 13 предоставляет общий storage runtime, scoped migrations, persistent job/checkpoint state, immutable outputs, durable OSM cache, PostGIS schema/indexes, retention API и backup/restore strategy.

Распределённый cache cluster, external backup scheduler, managed PostgreSQL topology, PITR infrastructure и high-availability replication относятся к deployment/platform operations и могут добавляться без изменения storage contracts.
