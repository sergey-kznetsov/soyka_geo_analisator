# Этап 13: универсальное PostgreSQL/PostGIS хранилище и кеш

Статус: технический этап завершён.

Geo Analyzer 2 не изменялся.

## Архитектурный результат

Этап 13 реализован как общий infrastructure layer для серверных программ экосистемы Геоанализатора, а не как локальная БД только СОЙКИ.

Создан независимый пакет `geoanalyzer_storage` версии `1.0.0`. Версия `soika_uds` повышена до `0.18.0`.

Общий слой разделён на:

- `ga_meta` — application registry и scoped migration journal;
- `ga_core` — jobs, stage checkpoints и immutable artifacts;
- `ga_cache` — application-scoped durable JSON cache с TTL;
- отдельные `ga_<application>` schemas для предметных данных конкретных серверных программ.

СОЙКА использует отдельную `ga_soika`. Общая platform migration не создаёт доменные таблицы СОЙКИ.

## Scoped migrations

Migrations разделены по scope:

- `platform:0001` — общий PostgreSQL/PostGIS storage contract;
- `soika:0001` — предметная схема СОЙКИ.

`ga_meta.schema_migrations` имеет primary key `(scope, version)`, поэтому разные приложения могут независимо иметь migration `0001`.

Runner:

- выполняет migrations транзакционно;
- сериализует параллельные запуски PostgreSQL advisory transaction lock;
- ограничивает ожидание `lock_timeout`;
- хранит SHA-256 каждой применённой migration;
- fail-closed при изменении уже применённого SQL;
- повторное применение неизменённого scope является no-op;
- не принимает migration из чужого scope.

`discover_migrations(scope, package=...)` позволяет будущей серверной программе хранить свой migration scope в собственном Python package.

## Canonical state и история

`PostgresJobStore` реализует существующий orchestration store protocol без изменения state machine:

- idempotent create;
- load/save;
- optimistic revision locking;
- lookup по idempotency key;
- list records.

Полный `JobRecord` остаётся canonical recovery state. Checkpoints зеркалируются в `ga_core.stage_checkpoints`.

Каждый завершённый stage output дополнительно записывается как immutable `ga_core.artifacts` с SHA-256. Идентичный повторный output не создаёт дубликат, а изменившийся output сохраняет отдельную историческую версию.

`PostgresArtifactStore` также поддерживает независимые artifacts с исходным GeoJSON и PostGIS geometry. Исходный GeoJSON участвует в content digest и сохраняется отдельно от PostGIS-normalized geometry. Artifact content глубоко immutable после создания.

## Durable cache и критерий повторного запуска

`PostgresJsonCache` использует ключ:

`application_id + namespace + deterministic SHA-256(operation, parameters)`.

Cache имеет:

- JSONB value;
- value SHA-256;
- TTL/`expires_at`;
- application isolation;
- namespace isolation;
- bounded cleanup.

Nominatim и Overpass принимают общий `ResponseCache` protocol. SQLite backend сохранён для локальной совместимости; production PostgreSQL cache подключается без изменения provider semantics.

Live integration test подтверждает основной критерий этапа: два одинаковых Nominatim lookup выполняют transport только один раз. Второй результат читается из PostgreSQL-cache и повторного скачивания нет.

Idempotent `PostgresJobStore.create_idempotent()` дополнительно предотвращает создание второго job для неизменившегося запроса.

## PostGIS и предметная схема СОЙКИ

`ga_soika` содержит типизированные таблицы для:

- model versions;
- source messages;
- preprocessed messages;
- classifications;
- geocoding results/candidates;
- events/event members;
- event connections;
- risk history.

Canonical lifecycle data не зависит от этих projections: полные stage outputs уже сохраняются в common core. `ga_soika` является предметной SQL/PostGIS поверхностью и может развиваться отдельными migrations.

Spatial contract использует SRID 4326. Созданы GiST indexes для exact geocoding points, geography distance lookup, event centroids и connection geometry.

## Retention и backup/restore

Добавлен application-scoped `RetentionManager` с bounded batches.

Technical defaults:

- completed jobs — 90 дней;
- failed jobs — 30 дней;
- cancelled jobs — 30 дней;
- cleanup batch — 5000 rows.

Это deployment defaults, а не юридически заданные сроки хранения.

Backup/restore baseline:

- `pg_dump --format=custom --no-owner --no-acl`;
- `pg_restore --clean --if-exists --no-owner --no-acl --exit-on-error`;
- password не передаётся в argv;
- поддерживаются обычные PostgreSQL endpoint forms, включая IPv6;
- production credentials должны поступать из secret infrastructure/`PGPASSFILE`.

Документация отдельно фиксирует off-host encrypted backups, периодический restore-test и возможность добавить WAL/PITR/HA на deployment layer без изменения application contracts.

## Проверенная среда

Live storage gate подтвердил:

- PostgreSQL `18.4`;
- PostGIS `3.6.4`;
- `platform` и `soika` migrations применяются и повторно являются no-op;
- SRID и GiST indexes существуют в реальной БД;
- application/cache isolation работает;
- TTL cache работает;
- одинаковый Nominatim lookup не вызывает повторный transport;
- optimistic PostgreSQL job store сохраняет orchestration contract;
- completed checkpoint становится immutable stage artifact;
- artifact GeoJSON/PostGIS round-trip сохраняет content digest.

## Финальная проверка кодового head

GitHub Actions подтвердил:

- Python compilation — passed;
- Ruff — passed;
- 272 deterministic unit/regression tests — passed;
- 8 live PostgreSQL/PostGIS integration tests — passed;
- `poetry.lock` consistency — passed;
- geolocation qualification workflow — passed;
- CPU Docker image build — passed;
- storage runtime (`geoanalyzer_storage`, `psycopg`, `psycopg_pool`) внутри CPU image — passed;
- CPU container start — passed;
- `/healthz` и `/readyz` — passed;
- GPU target build — passed.

## Критерий этапа

Технический критерий этапа 13 выполнен: неизменившийся запрос может быть восстановлен по idempotent job state, завершённые stage outputs сохраняются immutable, а повторный OSM lookup использует persistent PostgreSQL-cache и не скачивает данные повторно.

Хранилище является общим для экосистемы на уровне connection/migration/job/checkpoint/artifact/cache/retention contracts, при этом предметные schemas серверных программ остаются изолированными и независимо мигрируемыми.

Следующий технический этап по плану — этап 14: API для интерфейса.

Подробная эксплуатационная документация: `docs/STORAGE_PLATFORM.md`.
