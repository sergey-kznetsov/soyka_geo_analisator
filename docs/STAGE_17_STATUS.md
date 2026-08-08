# Этап 17: исправления после испытаний

Дата: 2026-08-08.

Статус: **завершён и интегрирован в Stage 16**. Stage 17 устранил release-blockers, найденные обязательным Stage 16 release-candidate gate, без ослабления security gates и без автоматического одобрения legacy ML-моделей. PR #25 объединён в `agent/stage16-release-candidate-testing` merge-коммитом `adc73ae6f1ef10c1fa3770dbe62143335bf95e19`.

## Исправления dependency/security

Stage 16 обнаружил 90 известных уязвимостей в 12 пакетах старого Poetry main lock. Stage 17 провёл прямой AST-аудит импортов production-кода и разделил серверный runtime и импортированное research-окружение `factfinder`.

Production `pyproject.toml`/`poetry.lock` теперь содержит только зависимости, необходимые нормализованному серверному контуру. Старые исследовательские версии сохранены отдельно в `requirements-legacy-research.txt` и запрещены для production worker/module API environment. Граница подробно описана в `docs/LEGACY_RESEARCH_ENVIRONMENT.md`.

Production dependency set обновлён, в том числе:

- `requests` 2.34.2;
- `torch` 2.13.0;
- `transformers` 5.14.1;
- `huggingface-hub` 1.24.0;
- `hdbscan` 0.8.44;
- `umap-learn` 0.5.12;
- `tqdm` 4.67.1;
- `pyproj` 3.7.2 зафиксирован как прямая зависимость spatial runtime;
- `defusedxml` 0.7.1 добавлен для безопасного разбора внешнего XML.

Повторный security gate на обновлённом lock прошёл: locked main runtime `pip-audit` и storage dependency audit не нашли известных уязвимостей; Bandit HIGH gate также прошёл.

## Hardening внешних данных

Controlled external probes теперь принимают только HTTPS URL с hostname, запрещают credentials в URL и отклоняют redirect, покидающий HTTPS transport. Для внешнего XML используется `defusedxml` вместо небезопасного `xml.etree.ElementTree.fromstring`.

Локальный `serve-probes` по умолчанию использует loopback `127.0.0.1`; Docker image продолжает явно передавать `--host 0.0.0.0`, поскольку его probe endpoint публикуется только в управляемой container boundary.

Оставшиеся Bandit B608 medium findings в worker queue относятся к статическим внутренним спискам колонок SQL. Все значения задания, worker ID, lease, статусы и ошибки продолжают передаваться psycopg как параметры `%s`; пользовательские данные в SQL identifiers/structure не интерполируются. Эти findings не подавляются allowlist-ом и сохраняются как reviewed static-analysis evidence.

## Geolocation drift, выявленный повторной квалификацией

После обновления безопасного dependency lock live qualification на публичном Nominatim выявила реальный ranking regression: для `ул. Тверская, д. 13` в Москве более высокий score получал дом `13` на `4-й Тверской-Ямской`, хотя в том же candidate set присутствовал точный объект на `Тверской улице, 13`.

Причина была в house-ranking: `_semantic_name()` отдавал имя `amenity/building` раньше structured `address.road`. Для точного объекта `Правительство Москвы, 13, Тверская улица` street similarity поэтому вычислялась против названия amenity, а у ложного кандидата — против похожего названия улицы.

Stage 17 исправил house-ranking без ослабления distance threshold: для house mention street similarity теперь в первую очередь вычисляется по структурированному `address.road`. Добавлена regression-проверка, в которой точный `Тверская улица, 13` обязан обгонять `4-я Тверская-Ямская улица, 13`, даже если точный candidate ниже в исходном Nominatim rank.

Live geolocation qualification после исправления прошла с `approved_for_production=true` для квалифицированного профиля `soika-geolocation-ru-v1`:

- samples: 24;
- extraction exact rate: 0.958333;
- resolution rate: 0.958333;
- kind accuracy: 0.958333;
- within-tolerance rate: 0.875;
- median distance: 137.009 м;
- p95 distance: 1146.096 м;
- Казань: resolution 1.0, within tolerance 0.875;
- Москва: resolution 0.875, within tolerance 0.875;
- Санкт-Петербург: resolution 1.0, within tolerance 0.875.

Geolocation qualification workflow также приведён в соответствие с новым безопасным lock: прежняя CI-проверка жёстко ожидала уязвимый `requests==2.31.0`, теперь она проверяет зафиксированный `requests==2.34.2`.

## Финальная квалификация Stage 17 перед интеграцией

На одном объединённом Stage 16+17 head перед retarget PR #25 прошли все обязательные контуры:

- `release-candidate` — success;
- `quality` — success целиком, включая CPU/GPU Docker build, start и health checks;
- live `geolocation-qualification` — success;
- 318 unit/contract/regression tests — passed;
- 8 module HTTP protocol tests — passed;
- 17 PostgreSQL/PostGIS, worker queue, load/soak и failure-injection tests — passed;
- PostgreSQL 18 backup/restore — passed;
- controlled-external evidence — passed;
- locked runtime dependency audit — passed;
- storage dependency audit — passed;
- Bandit HIGH severity — 0;
- automated review threads PR #25 — отсутствуют.

После интеграции Stage 17 в Stage 16 весь этот набор повторно запускается на финальном head PR #24 перед merge в `main`.

## Model qualification

Stage 17 не создаёт отсутствующие model/training/validation/calibration/drift artifacts. Ограничение Stage 8B остаётся fail-closed: legacy category/topic models не получают production approval только из-за обновления библиотек или зелёного regression suite. Неодобренный model-backed path должен оставаться недоступным для production использования до появления обязательных immutable evidence.

Квалифицированный geolocation-профиль Natasha/Nominatim не означает approval для legacy category/topic classification models: это отдельные qualification domains и отдельные gates.

## Критерий завершения

Stage 17 завершён: dependency/security blockers устранены, выявленный live geolocation ranking defect исправлен, regression/security/qualification gates прошли, PR #25 интегрирован в Stage 16. Финальный шаг перед `main` — повторная квалификация общего Stage 16+17 head в PR #24.
