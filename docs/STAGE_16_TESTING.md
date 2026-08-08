# Этап 16: полное тестирование release candidate

Дата проверки: 2026-08-08.

Статус: **release candidate заблокирован**. Функциональные, интеграционные, нагрузочные и recovery-контуры проходят, но обязательный dependency security gate выявил известные уязвимости в зафиксированных runtime-зависимостях. До устранения этих блокеров критерий завершения этапа 16 не выполнен и production release запрещён.

Geo Analyzer 2 на этом этапе не изменяется. Его универсальный module connector и собственный полный regression suite были квалифицированы на этапе 15; СОЙКА проверяет свою сторону общего protocol `1.0.0`. Для CI не добавляется межрепозиторный PAT/deploy key только ради тестирования закрытого соседнего репозитория.

## Release-candidate gate

Основной gate находится в `.github/workflows/release-candidate.yml` и запускается для pull request в `main` и вручную через `workflow_dispatch`.

Проверяемые контуры:

- compilation и Ruff для release-candidate test surface;
- полный `tests/unit`: unit, contracts, deterministic regressions, classifier, event и risk-formula tests;
- authenticated HTTP module protocol end-to-end проверки `submit/status/result`, partial result, validation и error semantics;
- controlled-external evidence по источникам `vk`, `ok`, `local-media`, `municipal-public`, `dzen`, `pikabu`, `rutube`;
- geolocation qualification evidence;
- PostgreSQL/PostGIS migrations и integration tests;
- параллельный queue load test;
- повторные soak cycles;
- failure injection через истечение worker lease и восстановление задания другим worker;
- реальный `pg_dump`/`pg_restore` с последующей проверкой восстановленной БД;
- Bandit static security scan;
- `pip-audit` по точным версиям main-группы из `poetry.lock` и отдельно по `requirements-storage.txt`.

Точные версии main-зависимостей для `pip-audit` экспортируются из Poetry lock скриптом `scripts/export_stage16_locked_requirements.py`. Скрипт не разрешает диапазоны заново и не подменяет lock текущими версиями PyPI.

## Результаты release candidate

Финальный квалификационный прогон Stage 16 показал:

- основной существующий workflow `quality` — passed целиком, включая unit/static, live PostgreSQL/PostGIS, dependency lock и Docker CPU/GPU environment;
- 306 unit/contract/regression tests — passed;
- 8 module HTTP protocol tests — passed;
- 17 PostgreSQL/PostGIS, worker queue, load/soak и failure-injection tests — passed;
- logical backup/restore PostgreSQL 18 + PostGIS — passed;
- controlled-external parser evidence — passed;
- geolocation qualification evidence — passed;
- Bandit HIGH severity gate — passed, HIGH findings: 0;
- storage dependency audit (`requirements-storage.txt`) — passed, известных уязвимостей не найдено;
- locked main runtime dependency audit — **failed**: 90 известных уязвимостей в 12 пакетах;
- итоговый dependency security gate — **failed**, как и требуется для блокировки release candidate.

Load test создаёт 64 задания и параллельно разбирает их восемью worker-потоками; каждое задание должно быть получено ровно один раз. Soak test выполняет 10 последовательных циклов по 8 заданий и после каждого цикла проверяет здоровье очереди. Failure injection принудительно просрочивает lease занятого задания и проверяет, что новый worker может безопасно его получить, а прежний владелец больше не может подтвердить выполнение.

Backup/restore выполняется клиентскими утилитами той же PostgreSQL 18 service image, что исключает несовместимость `pg_dump` старшего сервера с более старым клиентом. После восстановления проверяются migrations, наличие SOЙКА jobs и `geoanalyzer_storage.cli check`.

## Security blockers

`pip-audit` обнаружил **90 известных уязвимостей в 12 пакетах**, присутствующих в зафиксированной main-группе `poetry.lock`:

`flair 0.12.2`, `gdown 4.4.0`, `geopandas 0.12.2`, `nltk 3.8.1`, `protobuf 3.20.2`, `requests 2.31.0`, `scikit-learn 1.2.2`, `transformers 4.28.1`, `sentencepiece 0.1.99`, `tqdm 4.64.1`, `sqlitedict 2.1.0`, `torch 2.0.1`.

Часть advisories имеет опубликованные исправленные версии, часть старого ML-стека требует существенного обновления, а для отдельных advisories исправленная версия в базе не указана. Эти результаты нельзя подавлять allowlist-ом только для получения зелёного CI: сначала требуется определить реально используемые dependency paths, обновить или удалить уязвимые зависимости, повторно квалифицировать ML/runtime совместимость и затем повторить Stage 16 gate.

Bandit отдельно зафиксировал medium findings, которые не являются автоматически подтверждёнными уязвимостями, но должны быть разобраны при исправлениях: внешний bind probe server, XML parsing внешних данных, `urlopen` boundary и динамически формируемые SQL-фрагменты worker queue. HIGH findings отсутствуют.

## Ограничение model qualification

Этап 16 не отменяет fail-closed решение этапа 8B. Legacy category/topic models по-прежнему **не имеют production approval**, так как отсутствует полный набор обязательных immutable model/training/validation/calibration/drift evidence. Зеленые unit/regression tests не заменяют model qualification. Подробности: `docs/STAGE_8B_STATUS.md` и `docs/MODEL_QUALIFICATION.md`.

## Критерий завершения

Этап 16 можно пометить завершённым только после одновременного выполнения всех обязательных release-candidate gates. Текущее состояние — **BLOCKED** из-за dependency security audit и сохраняющегося fail-closed model qualification для legacy-моделей.

Устранение обнаруженных дефектов и повторная квалификация относятся к этапу 17 «Исправления после испытаний». До этого PR этапа 16 не должен утверждать production readiness.
