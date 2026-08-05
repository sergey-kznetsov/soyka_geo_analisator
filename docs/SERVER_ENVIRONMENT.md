# Воспроизводимое серверное окружение

## Назначение

Этот документ описывает второй этап разработки СОЙКА UDS Development: единое серверное окружение Python 3.11 для локальной проверки, CI и облачного развёртывания.

Окружение не является отдельным пользовательским приложением. Оно запускает внутренний процесс СОЙКИ и предоставляет только служебные проверки `/healthz` и `/readyz`. Пользовательский интерфейс остаётся в Geo Analyzer.

## Зафиксированные версии

- Python: `3.11.15`;
- базовый образ: официальный `python:3.11.15-slim-bookworm` с зафиксированным digest;
- Poetry: `2.4.1`;
- GDAL: ветка `3.6.x` из Debian Bookworm;
- GEOS: ветка `3.11.x` из Debian Bookworm;
- PROJ: ветка `9.1.x` из Debian Bookworm;
- морфологический анализатор: `pymorphy3 2.0.6`;
- русские словари: `pymorphy3-dicts-ru 2.4.417150.4580142`.

Полный набор Python-зависимостей фиксируется в `poetry.lock`. Изменение `pyproject.toml` без обновления lock-файла блокируется CI.

## Совместимость legacy-кода

Исходный геокодер выполняет `import pymorphy2`. Пакет `pymorphy2` не используется как внешняя зависимость. В репозитории находится небольшой совместимый адаптер, который экспортирует `pymorphy3.MorphAnalyzer` под прежним именем.

Это временная граница совместимости. Новые модули обязаны импортировать `pymorphy3` напрямую. Адаптер удаляется после полной переработки геокодера.

## Запуск CPU-профиля

```bash
docker compose --profile cpu up --build -d
curl http://127.0.0.1:18080/healthz
curl http://127.0.0.1:18080/readyz
```

Остановка:

```bash
docker compose --profile cpu down
```

## Запуск GPU-профиля

На сервере должны быть установлены совместимый драйвер NVIDIA и NVIDIA Container Toolkit.

```bash
docker compose --profile gpu up --build -d
curl http://127.0.0.1:18080/healthz
curl http://127.0.0.1:18080/readyz
```

GPU-профиль считается готовым только когда `torch.cuda.is_available()` возвращает `true`.

## Проверки

Локальная диагностика Python-окружения:

```bash
poetry run soika-uds doctor --strict --repository-root .
```

Проверка серверной готовности:

```bash
SOIKA_DATA_DIR=./var/data \
SOIKA_MODEL_DIR=./var/models \
poetry run soika-uds ready --strict --repository-root .
```

Служебный процесс проверок:

```bash
poetry run soika-uds serve-probes --host 127.0.0.1 --port 8080 --repository-root .
```

## Модели

Исходный манифест находится в `soika_uds/resources/model_manifest.json`. Mutable-ссылки `main` нельзя использовать непосредственно в серверной установке.

Сначала создаётся lock-манифест с точными commit SHA:

```bash
poetry run soika-uds models lock \
  --output ./var/model-manifest.lock.json
```

Затем модели скачиваются по точным revision и для каждой директории рассчитывается SHA-256:

```bash
poetry run soika-uds models install \
  --manifest ./var/model-manifest.lock.json \
  --destination ./var/models
```

Проверка целостности:

```bash
poetry run soika-uds models verify \
  --destination ./var/models \
  --strict
```

Лицензии моделей со значением `UNKNOWN_REQUIRES_REVIEW` или `CC_REQUIRES_REVIEW` должны быть проверены до производственного использования и распространения файлов моделей.

## Безопасность контейнера

Контейнер:

- работает от пользователя `soika` с UID/GID `10001`;
- использует read-only root filesystem в Docker Compose;
- удаляет Linux capabilities;
- включает `no-new-privileges`;
- хранит данные и модели в отдельных volumes;
- публикует порт по умолчанию только на `127.0.0.1`;
- не содержит компиляторов в runtime-слое;
- запускается через `tini`;
- не загружает модели в healthcheck.

## Критерий завершения этапа

Этап считается завершённым, когда одновременно выполнено следующее:

1. `poetry.lock` создан на Python 3.11 и соответствует `pyproject.toml`.
2. Unit-тесты и Ruff проходят на Python 3.11.15.
3. CPU и GPU targets Dockerfile успешно собираются в CI.
4. CPU-контейнер проходит `/healthz` и `/readyz`.
5. `pymorphy2` отсутствует среди внешних зависимостей, legacy-импорт работает через проверяемый адаптер `pymorphy3`.
6. Команды блокировки, установки и проверки моделей покрыты unit-тестами без сетевых обращений.
