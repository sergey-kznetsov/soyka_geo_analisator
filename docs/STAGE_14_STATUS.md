# Этап 14: серверные worker и эксплуатация

Статус: реализация подготовлена, qualification выполняется в PR.

Geo Analyzer 2 не изменялся.

## Реализованный контур

Добавлен отдельный пакет `soika_uds.worker` и durable PostgreSQL migration scope `worker`.

Worker runtime включает:

- PostgreSQL job queue с коротким `FOR UPDATE SKIP LOCKED` claim;
- отдельные CPU/GPU compute classes;
- queue lease и heartbeat;
- продление orchestration lease для долгих стадий;
- worker-level retry/backoff и explicit retry;
- cooperative cancellation;
- SIGTERM/SIGINT graceful shutdown;
- wall-clock timeout;
- fail-closed проверку cgroup memory limit;
- JSON structured logs с redaction secret-like fields;
- Prometheus metrics;
- W3C trace correlation;
- machine-readable alert sink;
- private health/readiness/metrics probes;
- backend-only transport-neutral submit/cancel/retry control.

## Storage contract

Stage 13 migration `platform:0001` не изменяется.

Stage 14 добавляет независимый scope:

`worker:0001_worker_queue`

`ga_core.job_queue` хранит только scheduling state. Canonical state анализа остаётся в `ga_core.jobs`/`JobRecord`.

## Отказоустойчивость

Worker обрабатывает один queue item за раз. Failure текущего executor не завершает polling loop: item получает retry/exhausted state, после чего следующий job может выполняться независимо.

Queue lease и orchestration lease не держат открытую transaction на время ML/OSM обработки. Если worker погибает, истечение lease позволяет другому worker восстановить job.

## Deployment isolation

Добавлен `docker-compose.workers.yml`:

- CPU/GPU worker разделены;
- host ports отсутствуют;
- доступ только через `geoanalyzer_backend` network;
- DSN передаётся Docker secret file;
- read-only filesystem, `cap_drop: ALL`, `no-new-privileges`;
- CPU/GPU memory/CPU limits;
- `pids_limit`;
- two-minute stop grace.

Stable authenticated transport Geo Analyzer ↔ СОЙКА остаётся этапом 15 и не подменяется публичным endpoint на этапе 14.

## Версии

- `soika-uds-development`: `0.19.0`;
- `geoanalyzer_storage`: `1.1.0`.

## Qualification

Добавлены deterministic unit/regression tests и live PostgreSQL tests для:

- CPU/GPU routing;
- priority order;
- concurrent distinct claim;
- retry/exhaustion/reset;
- cancellation;
- isolation failure одного job от следующего;
- timeout;
- graceful stop;
- trace/log/metric contracts;
- secret-file и memory-limit boundaries;
- worker migration/index contract.

Финальные количества тестов и CI gates будут зафиксированы после завершения PR review.

## Критерий этапа

Критерий считается выполненным после зелёного live gate и review: отказ одного задания не прекращает worker polling loop и не изменяет состояние других заданий; Geo Analyzer backend взаимодействует только через существующий canonical orchestration state, поэтому ошибка SOIKA job не должна нарушать основной отчёт Geo Analyzer.

Эксплуатационная документация: `docs/WORKER_RUNTIME.md`.
