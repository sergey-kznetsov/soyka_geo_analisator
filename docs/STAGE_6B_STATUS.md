# Этап 6B: реализация адаптеров

Статус: выполнен 5 августа 2026 года.

Область изменений ограничена репозиторием СОЙКА UDS Development. Geo Analyzer 2 не изменялся.

Реализовано:

- `VkApiAdapter` для официального API VK;
- `OkApiAdapter` и изолированный `OkMd5Signer` для официального API Одноклассников;
- `HtmlConnectorAdapter` для разрешённых HTML, RSS/Atom и sitemap;
- адаптеры для локальных СМИ, муниципальных публичных источников, Дзен, Pikabu и RUTUBE;
- регистрация всех семи адаптеров в существующем `ParserRegistry`;
- выполнение через существующий `ParserRunner`;
- source-specific checkpoint;
- стабильные внешние идентификаторы;
- fixture-based и mock-transport integration tests;
- controlled external probe по одному публичному запросу на источник;
- версия пакета `0.8.0`.

Общий контракт идентификаторов источников синхронизирован с каталогом коннекторов: двухсимвольный идентификатор `vk` поддерживается `SourcePolicy` и `ParserRequest`.

## Fixture и mock-transport результат

Все семь адаптеров запущены через штатный `ParserRunner`. Получено 12 нормализованных `SourceMessage`:

- VK — 2;
- Одноклассники — 1;
- локальные СМИ — 1;
- муниципальные публичные источники — 2;
- Дзен — 2;
- Pikabu — 2;
- RUTUBE — 2.

У всех источников статус `completed`; дубликаты, отклонённые сообщения и ошибки отсутствуют. Сохранены coverage, итоговые checkpoint, внешние идентификаторы, тексты, даты, ссылки и разрешённые metadata.

Полный результат: `docs/STAGE_6B_FIXTURE_RESULTS.json`.

## Controlled external результат

Каждый из семи targets вернул классифицированный HTTP-результат:

- VK — `200`, публичная страница сообщества;
- Одноклассники — `200`, заголовок и описание официального сообщества Республики Татарстан;
- локальные СМИ — `200`, заголовок и описание «Татар-информ»;
- муниципальный портал Казани — `403 Forbidden` на официальном URL и официальном поддомене;
- Дзен — `200`, перенаправление на SSO-страницу;
- Pikabu — `200`, читаемый результат с учётом `windows-1251`;
- RUTUBE — `200`, заголовок и описание канала Мэрии Казани.

Ограничения `403` и SSO не обходились и не заменялись фиктивными сообщениями. Они остаются диагностическим результатом controlled external test.

Полный результат: `docs/STAGE_6B_EXTERNAL_RESULTS.json`.

## Проверка

- Python 3.11 compilation;
- Ruff;
- 111 deterministic unit, contract, orchestration, parser-platform и connector-adapter tests;
- `poetry.lock` consistency;
- CPU Docker build и запуск;
- `/healthz` и `/readyz`;
- GPU Docker target build;
- fixture result для всех семи адаптеров;
- controlled external report для всех семи источников.

Production-сбор VK и OK остаётся API-only и требует credentials владельца развёртывания. Профили конкретных сайтов должны пройти отдельные legal, robots и selector review до включения production-сети.
