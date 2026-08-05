# Этап 6B: реализация адаптеров

Статус: реализован, выполняется финальная проверка.

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

Статическая нормализация исходников завершена. Остаточные замечания Ruff устранены точечными изменениями без изменения поведения адаптеров.

Production-сбор VK и OK остаётся API-only и требует credentials владельца развёртывания. Controlled external probe публичной страницы не считается заменой production API.

Финальный статус будет установлен после полного CI, Docker-проверки и сохранения controlled external report для всех семи источников.
