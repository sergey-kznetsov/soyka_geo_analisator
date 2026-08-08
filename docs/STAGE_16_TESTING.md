# Этап 16: полное тестирование release candidate

Дата проверки: 2026-08-08.

Статус: **обязательный release-candidate gate пройден после исправлений Stage 17**. Функциональные, интеграционные, нагрузочные, recovery и dependency-security контуры проходят. Это не отменяет отдельное fail-closed ограничение model qualification: legacy category/topic models остаются недоступными для production approval без обязательных immutable evidence.

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

Точные версии main-зависимостей для `pip-audit` экспортируются из Poetry lock скриптом `scripts/export_stage16_locked_requirements.py`. После Stage 17 exporter поддерживает platform-specific Poetry entries и group-specific markers, при этом остаётся fail-closed для неоднозначных unmarked версий.

## Результаты после Stage 17 remediation

Подтверждённый повторный release-candidate test surface показал:

- 318 unit/contract/regression tests — passed;
- 8 module HTTP protocol tests — passed;
- 17 PostgreSQL/PostGIS, worker queue, load/soak и failure-injection tests — passed;
- logical backup/restore PostgreSQL 18 + PostGIS — passed;
- controlled-external parser evidence — passed;
- geolocation qualification evidence verification — passed;
- Bandit HIGH severity gate — passed, HIGH findings: 0;
- storage dependency audit (`requirements-storage.txt`) — passed;
- locked main runtime dependency audit — passed, известных уязвимостей не найдено;
- итоговый dependency security gate — passed.

Load test создаёт 64 задания и параллельно разбирает их восемью worker-потоками; каждое задание должно быть получено ровно один раз. Soak test выполняет 10 последовательных циклов по 8 заданий и после каждого цикла проверяет здоровье очереди. Failure injection принудительно просрочивает lease занятого задания и проверяет, что новый worker может безопасно его получить, а прежний владелец больше не может подтвердить выполнение.

Backup/restore выполняется клиентскими утилитами той же PostgreSQL 18 service image, что исключает несовместимость `pg_dump` старшего сервера с более старым клиентом. После восстановления проверяются migrations, наличие СОЙКА jobs и `geoanalyzer_storage.cli check`.

## Исправленный dependency blocker

Первоначальный Stage 16 `pip-audit` обнаружил 90 известных уязвимостей в 12 пакетах старого main lock. Stage 17 не добавлял allowlist для этих advisories. Вместо этого был выполнен аудит реального dependency usage, историческое research-окружение `factfinder` отделено от production runtime, а необходимые production зависимости обновлены и заново зафиксированы.

Исторические версии сохранены только в `requirements-legacy-research.txt` для изолированного воспроизведения старых экспериментов. Они не входят в production Poetry main group, Docker image, worker/module API runtime или release-candidate dependency gate. Подробности: `docs/LEGACY_RESEARCH_ENVIRONMENT.md` и `docs/STAGE_17_STATUS.md`.

Внешний XML переведён на `defusedxml`, controlled external URL boundary ограничена HTTPS, а локальный probe server по умолчанию использует loopback. Оставшиеся Bandit B608 medium findings в worker queue относятся к статическим внутренним спискам колонок; фактические значения передаются через psycopg parameters `%s` и не интерполируются в структуру SQL.

Повторная live geolocation qualification после обновления lock дополнительно выявила ranking defect для house candidates: `amenity/building` мог вытеснить структурированное `address.road` при сравнении названия улицы. Stage 17 исправляет house ranking так, чтобы точная structured road match имела приоритет без снижения qualification thresholds.

## Ограничение model qualification

Этапы 16 и 17 не отменяют fail-closed решение этапа 8B. Legacy category/topic models по-прежнему **не имеют production approval**, так как отсутствует полный набор обязательных immutable model/training/validation/calibration/drift evidence. Зелёные unit/regression/security tests не заменяют model qualification. Подробности: `docs/STAGE_8B_STATUS.md` и `docs/MODEL_QUALIFICATION.md`.

Это ограничение не делает release-candidate gate красным: неподтверждённые model-backed paths должны оставаться закрытыми. Production использование конкретной ML-модели разрешается только после прохождения её отдельного qualification gate.

## Критерий завершения

Обязательные Stage 16 test/security contours после Stage 17 remediation проходят. Завершение Stage 17 требует финального прохождения repository quality/geolocation workflows на одном head commit, проверки automated review и интеграции remediation поверх Stage 16 branch. После этого Stage 16 и Stage 17 можно объединять в `main`, сохраняя fail-closed model restriction.
