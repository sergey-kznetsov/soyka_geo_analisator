# Серверные worker СОЙКИ

Документ описывает эксплуатационный runtime этапа 14. Стабильный внешний transport Geo Analyzer ↔ СОЙКА относится к этапу 15; здесь реализован transport-neutral backend control и закрытый worker runtime.

## Архитектура

Worker runtime состоит из пяти независимых частей:

1. `ga_core.job_queue` — durable PostgreSQL-очередь в migration scope `worker`;
2. `PostgresJobQueue` — enqueue/claim/renew/release/ack/cancel/retry;
3. `WorkerRuntime` — один изолированный job за раз, heartbeat, retry, graceful shutdown;
4. `WorkerControl` — внутренний Python API backend для submit/cancel/retry;
5. observability — JSON logs, Prometheus metrics, W3C trace correlation и alerts.

Очередь не заменяет `ga_core.jobs`. `JobRecord` остаётся canonical state конечного автомата. Queue хранит только operational scheduling state.

## Migration scope

Перед worker runtime применяются scopes:

```text
platform -> soika -> worker
```

`worker:0001_worker_queue` создаёт `ga_core.job_queue`. Scope отделён от `platform`, поэтому утверждённый Stage 13 baseline `platform:0001` не изменяется.

Migration runner проверяет checksum и отсутствие исчезнувших migration, поэтому history остаётся fail-closed.

## Claim и конкуренция

Claim выполняется короткой PostgreSQL-транзакцией:

```sql
SELECT ... FOR UPDATE SKIP LOCKED
```

после чего выбранная строка получает lease и транзакция завершается. Worker не держит row lock или открытую DB transaction во время NLP, OSM или другой долгой стадии.

Порядок выбора:

1. `priority DESC`;
2. `available_at`;
3. `enqueued_at`;
4. `analysis_id`.

CPU worker читает только `compute_class=cpu`, GPU worker — только `compute_class=gpu`.

## Leases и восстановление

Есть два независимых lease:

- queue lease — исключает одновременное выполнение одного queue item разными worker;
- orchestration lease — исключает одновременный `SoikaOrchestrator.resume()` одного job.

Оба lease продлеваются heartbeat. Если процесс погибает, lease истекает и задание снова становится доступно без ручного снятия lock.

Queue lease по умолчанию — 600 секунд, heartbeat — 30 секунд. Конфигурация запрещает lease короче двух heartbeat interval.

Любая ошибка queue heartbeat трактуется fail-closed как неопределённое владение lease: worker выставляет cancellation/lease-lost state, создаёт alert и после executor не выполняет `ack`. Это исключает продолжение работы с недоказанным правом владения queue item.

## Retry

Worker-level retry используется только для infrastructure failure вокруг выполнения job: timeout, process/runtime error или потеря ресурса. Stage-level retry внутри оркестратора остаётся отдельным механизмом.

Worker queue хранит `attempt`, `max_attempts`, `available_at` и `last_error`. Backoff по умолчанию:

- initial: 5 секунд;
- multiplier: 2;
- max: 300 секунд.

После exhaustion queue item больше не claim-ится автоматически. Recovery разделён по canonical state:

- если `JobRecord.status == failed`, queue row сохраняется exhausted вместе с CPU/GPU routing; explicit retry сначала сбрасывает queue item, затем вызывает canonical `retry_failed()`;
- если infrastructure failure исчерпал worker attempts до перехода canonical job в `failed`, backend может выполнить queue-only retry, но только при отсутствии живого orchestration lease; pipeline checkpoints при этом не сбрасываются;
- живой orchestration lease блокирует ручной queue-only retry через `JobLeaseError`.

Если canonical reset после queue reset неожиданно завершается ошибкой, queue fail-closed переводится в cancelled state, а исходная ошибка возвращается вызывающему коду.

## Отмена

Backend вызывает `WorkerControl.cancel()`:

1. canonical `JobRecord.cancel_requested` устанавливается через `SoikaOrchestrator`;
2. queue item получает `cancel_requested=true`;
3. queued item больше не claim-ится;
4. для active item heartbeat устанавливает `WorkerContext.cancellation`;
5. `OrchestratorExecutor` переносит cancel request в конечный автомат на безопасной stage boundary.

Принудительное убийство Python-потока не используется.

## Graceful shutdown

SIGTERM/SIGINT не начинают новое задание. Worker немедленно становится `not_ready`, но активный job получает возможность завершить текущую работу.

Deployment задаёт `stop_grace_period: 2m`. Если platform после grace убивает контейнер, queue/orchestration leases обеспечивают последующее восстановление.

## Лимиты времени

`wall_timeout_seconds` по умолчанию 3600 секунд. На Unix main thread используется `ITIMER_REAL`/SIGALRM. `WorkerTimeoutError` является отдельным control-flow exception и выходит наружу из обычных `except Exception` stage handlers, после чего worker requeue-ит только текущий job.

Hard isolation при зависании native/C code остаётся обязанностью container runtime: если Python signal handler не может выполниться, platform завершает контейнер, а lease делает job доступным после expiry.

## Лимиты памяти

Production Compose задаёт отдельные memory limits для CPU и GPU worker. Worker может быть запущен с `--require-memory-limit-mb`; при этом он читает cgroup v2/v1 limit и fail-closed, если limit unlimited или выше разрешённого.

Технические defaults в `docker-compose.workers.yml`:

- CPU: 4 GiB, 2 CPU;
- GPU: 12 GiB host memory, 4 CPU + GPU reservation;
- `pids_limit: 512`;
- read-only root filesystem;
- `cap_drop: ALL`;
- `no-new-privileges`.

Это deployment defaults, а не универсальная оценка памяти каждой модели.

## Secrets

Worker CLI не имеет DSN argument. PostgreSQL DSN читается только из `GEOANALYZER_DATABASE_DSN_FILE`/`--database-dsn-file`.

Compose передаёт DSN через Docker secret в `/run/secrets/geoanalyzer_database_dsn`. Structured logging автоматически редактирует поля, имя которых похоже на `password`, `secret`, `token`, `dsn` или `credential`.

Model/repository credentials должны передаваться аналогичным secret infrastructure, а не через command line или committed environment files.

## Сеть и probes

`docker-compose.workers.yml`:

- не публикует worker ports на host;
- подключает worker только к выделенной external network `geoanalyzer_backend`;
- `/healthz`, `/readyz`, `/metrics` доступны только внутри этой network;
- worker Compose healthcheck явно использует `127.0.0.1:9090/healthz`, а не inherited application probe на 8080;
- standalone probe server по умолчанию разрешает только loopback bind; remote bind требует явный `--allow-remote-probes`.

На этапе 14 worker не предоставляет публичный submit/cancel HTTP endpoint. Это исключает случайное обходное API. Transport и аутентификация backend Geo Analyzer вводятся на этапе 15.

## Structured logs

Каждая строка — JSON object с UTC timestamp, level, event и correlation fields. Worker не логирует request payload целиком и не включает DSN.

Основные events:

- `worker.started`;
- `worker.job.claimed`;
- `worker.job.completed`;
- `worker.job.failed`;
- `worker.job.domain_failed`;
- `worker.lease.heartbeat_error`;
- `worker.shutdown.requested`;
- `worker.loop.error`;
- `worker.alert`;
- `trace.span.start` / `trace.span.end`.

## Metrics

`/metrics` выдаёт Prometheus text exposition без внешней Python-зависимости. Метрики имеют labels `worker_id` и `compute_class`.

Основные series:

- claimed/completed/failed/requeued/exhausted jobs;
- timeout, lease-conflict и lease-heartbeat-error counters;
- active jobs;
- ready/leased/delayed/exhausted/cancelled queue gauges;
- oldest ready age, включая ready rows с истёкшим lease;
- worker readiness/up state;
- loop error counter.

## Tracing

Trace correlation следует формату W3C `traceparent`: 32-hex `trace_id`, 16-hex `span_id`, trace flags. Queue хранит `trace_id`, поэтому retry и worker takeover продолжают один analysis trace.

Этап 14 не добавляет vendor-specific tracing backend. Structured span events можно преобразовать в OpenTelemetry/OTLP на deployment layer без изменения job/queue contracts.

## Alerts

`AlertSink` является интерфейсом. Default `LoggingAlertSink` создаёт machine-readable `worker.alert`. Alert генерируется для:

- потери или неопределённости queue lease;
- exhausted worker retry;
- burst repeated failures;
- worker-loop infrastructure failure.

Webhook/email/PagerDuty routing относится к deployment monitoring и подключается реализацией `AlertSink` либо сборщиком логов.

## Executor factory

`SOIKA_WORKER_EXECUTOR=module.path:factory` обязателен. Factory получает `PostgresDatabase` и `WorkerSettings`, возвращает callable `WorkerExecutor`.

Runtime намеренно не создаёт скрытый default pipeline: production model gates и конкретная композиция handlers должны быть утверждены отдельно. Это сохраняет fail-closed поведение незаквалифицированных моделей.

## Критерий отказоустойчивости

Основная инварианта этапа: exception/timeout одного job обрабатывается внутри worker boundary, его queue item requeue/exhausted независимо, а polling loop продолжает брать следующие задания. Live PostgreSQL integration подтверждает, что concurrent worker claim-ят разные rows, retry/exhaustion восстанавливаются, а expired leases снова учитываются как ready work и в queue-age monitoring.
