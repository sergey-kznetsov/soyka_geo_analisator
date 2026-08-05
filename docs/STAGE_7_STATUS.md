# Этап 7: preprocessing и дедупликация

Статус: реализован, выполняется финальная проверка.

Область изменений ограничена репозиторием СОЙКА UDS Development. Geo Analyzer 2 не изменялся.

Реализовано:

- отдельный пакет `soika_uds.preprocessing`;
- неизменяемые контракты preprocessing result;
- сохранение исходного текста и исходного timestamp;
- безопасная очистка HTML без исполнения содержимого;
- исключение script/style/noscript/template;
- Unicode NFKC и удаление control/zero-width символов;
- нормализация пробелов и переводов строк;
- удаление только точных allowlisted технических строк;
- выделение HTML, `>` и `[quote]` цитат;
- отдельные `normalized_text` и `model_text`;
- детерминированное определение Cyrillic/Latin языка;
- нормализация timezone-aware timestamp в UTC;
- fail-closed обработка naive timestamp;
- canonical URL без fragment и tracking parameters;
- SHA-256 identity, URL и text fingerprints;
- 64-bit SimHash и banded near-duplicate index;
- `unique`, `technical_duplicate`, `cross_source_repost`, `repeated_appeal`;
- сохранение исключённых дублей и повторных обращений;
- независимость результата от порядка входных сообщений;
- полный transformation trace с input/output SHA-256 каждого шага;
- `PreprocessingStageHandler` для `PipelineStage.PREPROCESSING`;
- версия пакета `0.9.0`;
- unit и orchestration integration tests.

Подробное описание: `docs/PREPROCESSING.md`.

Финальный статус будет установлен после обязательного CI, lock consistency и CPU/GPU Docker-проверки.
