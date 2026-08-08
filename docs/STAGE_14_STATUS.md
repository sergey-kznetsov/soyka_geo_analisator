# Этап 14: серверные worker и эксплуатация

Статус: завершён 8 августа 2026 года.

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

Storage package повышен до `geoanalyzer_storage 1.1.0`, SOIKA — до `0.19.0`.

## Отказоустойчивость и recovery

Worker обрабатывает один queue item за раз. Failure текущего executor не завершает polling loop: item получает retry/exhausted state, после чего следующий job может выполняться независимо.

Queue lease и orchestration lease не держат открытую transaction на время ML/OSM обработки. Если worker погибает, истечение lease позволяет другому worker восстановить job.

Любая ошибка queue heartbeat трактуется fail-closed: владение lease считается недоказанным, устанавливаются cancellation/lease-lost state и alert, а worker не выполняет `ack` после executor.

Canonical `FAILED` сохраняет exhausted queue row вместе с CPU/GPU routing. Explicit retry сначала восстанавливает queue item и затем сбрасывает failed `JobRecord`; если canonical reset не проходит, queue возвращается в fail-closed cancelled state.

Infrastructure exhaustion до canonical `FAILED` также восстановим: backend может выполнить queue-only retry после отсутствия или истечения orchestration lease. Живой orchestration lease блокирует такой retry. Pipeline checkpoint state при queue-only recovery не сбрасывается.

Queue-age monitoring использует тот же ready predicate, что и claim: строки с истёкшим lease снова считаются ready и участвуют в `oldest_ready_age_seconds`.

Infrastructure error в polling loop не вызывает tight retry loop: worker применяет штатный `poll_seconds` delay перед следующей попыткой обращения к очереди.

Повторный idempotent submit уже terminal job не создаёт новую queue row и возвращает canonical record без повторного scheduling.

## Deployment isolation

Добавлен `docker-compose.workers.yml`:

- CPU/GPU worker разделены;
- host ports отсутствуют;
- доступ только через `geoanalyzer_backend` network;
- DSN передаётся Docker secret file;
- read-only filesystem и read-only model volume;
- `cap_drop: ALL`, `no-new-privileges`;
- CPU/GPU memory/CPU limits;
- `pids_limit`;
- two-minute `stop_grace_period`;
- worker healthcheck явно использует `127.0.0.1:9090/healthz`.

Grace period исполняется process/container supervisor через Compose `stop_grace_period`; неисполняемая in-process настройка `shutdown_grace_seconds` удалена из `WorkerSettings`, CLI и runtime.

Stable authenticated transport Geo Analyzer ↔ СОЙКА остаётся этапом 15 и не подменяется публичным endpoint на этапе 14.

## Automated review

Закрыты девять замечаний automated review: шесть P1 и три P2.

1. failed canonical job больше не теряет queue routing перед explicit retry;
2. любая queue-heartbeat ошибка трактуется как lease uncertainty fail-closed;
3. worker Compose не наследует application healthcheck на 8080 и проверяет собственный probe на 9090;
4. exhausted infrastructure failure имеет поддерживаемый queue-only recovery path после истечения orchestration lease;
5. `oldest_ready_age_seconds` учитывает ready rows с истёкшим lease;
6. polling loop делает backoff после infrastructure/claim errors и не создаёт tight retry loop;
7. terminal idempotent submit не создаёт невыполнимые queue rows;
8. shutdown grace закреплён на enforceable supervisor layer вместо фиктивной in-process настройки;
9. stale `shutdown_grace_seconds` удалён из CLI, runtime и тестовых fixtures, поэтому worker startup снова соответствует `WorkerSettings` contract.

Все девять review threads resolved и покрыты unit/live regression tests.

## Проверенная среда

GitHub Actions на финальном кодовом head подтвердил:

- Python compilation — passed;
- Ruff — passed;
- 298 deterministic unit/regression tests — passed;
- 14 live PostgreSQL/PostGIS integration tests — passed;
- `platform`, `soika` и `worker` migrations — passed;
- worker migration reapply — no-op;
- concurrent workers claim разные queue rows;
- CPU/GPU routing и priority order — passed;
- retry/exhaustion/cancel/expired-lease recovery — passed;
- `poetry.lock` consistency — passed;
- worker Compose private-network/isolation contract — passed;
- worker healthcheck port 9090 contract — passed;
- CPU Docker image build и storage/worker import — passed;
- CPU container `/healthz` и `/readyz` — passed;
- GPU target build — passed.

## Критерий этапа

Критерий этапа 14 выполнен: ошибка, timeout или исчерпание попыток одного задания изолируются внутри worker/queue boundary и не прекращают polling loop; другие задания продолжают выполняться независимо.

Canonical analysis state остаётся в существующем orchestration/storage contract, а worker runtime не предоставляет отдельного публичного API. Поэтому отказ SOIKA job не требует менять основной отчёт Geo Analyzer и не создаёт обходного transport до этапа 15.

Следующий технический этап по плану — этап 15: интеграция СОЙКИ в Geo Analyzer.

Эксплуатационная документация: `docs/WORKER_RUNTIME.md`.
