# Этап 17: исправления после испытаний

Дата: 2026-08-08.

Этап 17 устраняет release-blockers, найденные обязательным Stage 16 release-candidate gate. Изменения выполняются без ослабления security gates и без автоматического одобрения legacy ML-моделей.

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

Повторный Stage 16 security gate на обновлённом lock прошёл: locked main runtime `pip-audit` и storage dependency audit не нашли известных уязвимостей; Bandit HIGH gate также прошёл.

## Hardening внешних данных

Controlled external probes теперь принимают только HTTPS URL с hostname, запрещают credentials в URL и отклоняют redirect, покидающий HTTPS transport. Для внешнего XML используется `defusedxml` вместо небезопасного `xml.etree.ElementTree.fromstring`.

Локальный `serve-probes` по умолчанию использует loopback `127.0.0.1`; Docker image продолжает явно передавать `--host 0.0.0.0`, поскольку его probe endpoint публикуется только в управляемой container boundary.

Оставшиеся Bandit B608 medium findings в worker queue относятся к статическим внутренним спискам колонок SQL. Все значения задания, worker ID, lease, статусы и ошибки продолжают передаваться psycopg как параметры `%s`; пользовательские данные в SQL identifiers/structure не интерполируются. Эти findings не подавляются allowlist-ом и сохраняются как reviewed static-analysis evidence.

## Geolocation drift, выявленный повторной квалификацией

После обновления безопасного dependency lock live qualification на публичном Nominatim выявила реальный ranking regression: для `ул. Тверская, д. 13` в Москве более высокий score получал дом `13` на `4-й Тверской-Ямской`, хотя в том же candidate set присутствовал точный объект на `Тверской улице, 13`.

Причина была в house-ranking: `_semantic_name()` отдавал имя `amenity/building` раньше structured `address.road`. Для точного объекта `Правительство Москвы, 13, Тверская улица` street similarity поэтому вычислялась против названия amenity, а у ложного кандидата — против похожего названия улицы.

Stage 17 исправляет house-ranking без ослабления distance threshold: для house mention street similarity теперь в первую очередь вычисляется по структурированному `address.road`. Добавлена regression-проверка, в которой точный `Тверская улица, 13` обязан обгонять `4-я Тверская-Ямская улица, 13`, даже если точный candidate ниже в исходном Nominatim rank.

Geolocation qualification workflow также приведён в соответствие с новым безопасным lock: прежняя CI-проверка жёстко ожидала уязвимый `requests==2.31.0`, теперь она проверяет зафиксированный `requests==2.34.2`.

## Release-candidate regression

После remediation подтверждён release-candidate test surface:

- 318 unit/contract/regression tests — passed;
- module protocol end-to-end — passed;
- controlled-external evidence — passed;
- PostgreSQL/PostGIS, queue load/soak, failure injection и backup/restore — passed;
- locked runtime dependency audit — passed;
- storage dependency audit — passed;
- Bandit HIGH severity — 0.

Финальная live geolocation qualification должна проходить на том же head commit; её результат является обязательным перед интеграцией Stage 17 в Stage 16.

## Model qualification

Stage 17 не создаёт отсутствующие model/training/validation/calibration/drift artifacts. Ограничение Stage 8B остаётся fail-closed: legacy category/topic models не получают production approval только из-за обновления библиотек или зелёного regression suite. Неодобренный model-backed path должен оставаться недоступным для production использования до появления обязательных immutable evidence.

## Критерий завершения

Stage 17 считается завершённым после финального повторного прохождения всех CI/qualification gates на одном head commit, проверки automated review и интеграции remediation поверх Stage 16 branch. После этого Stage 16 release-candidate status может быть обновлён с dependency-blocked на технически зелёный, при сохранении fail-closed model restriction.
