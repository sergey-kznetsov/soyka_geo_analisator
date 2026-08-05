# Этап 7: preprocessing и дедупликация

Статус: выполнен 5 августа 2026 года.

Область изменений ограничена репозиторием СОЙКА UDS Development. Geo Analyzer 2 не изменялся.

Реализовано:

- отдельный пакет `soika_uds.preprocessing`;
- неизменяемые контракты preprocessing result;
- сохранение исходного текста и исходного timestamp;
- безопасная очистка HTML без исполнения содержимого;
- исключение `script`, `style`, `noscript` и `template`;
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
- фиксированный приоритет identity → cross-source → repeated appeal;
- сохранение исключённых дублей и повторных обращений;
- независимость результата от порядка входных сообщений;
- полный transformation trace с input/output SHA-256 каждого шага;
- `PreprocessingStageHandler` для `PipelineStage.PREPROCESSING`;
- JSON-compatible checkpoint output;
- версия пакета `0.9.0`;
- unit и orchestration integration tests.

## Подтверждённые свойства

- `original_text` не изменяется, включая символы, которые NFKC преобразует в model text;
- структурно валидные, но отклонённые сообщения сохраняются с причинами;
- malformed collection output не интерпретируется частично;
- повторная доставка того же source/external ID всегда является техническим дублем;
- одинаковый текст на другой платформе остаётся межисточниковым репостом;
- повторным обращением может стать только запись того же источника с другим external ID;
- порядок входного массива не меняет представителя, решения и output digest.

Подробное описание: `docs/PREPROCESSING.md`.

## Финальная проверка

- Python 3.11 compilation — success;
- Ruff — success;
- 126 deterministic unit, contract, orchestration, parser-platform, connector и preprocessing tests — success;
- `poetry.lock` consistency — success;
- CPU Docker build и запуск — success;
- `/healthz` и `/readyz` — success;
- GPU Docker target build — success.

Финальный CI выполнен на итоговом diff PR этапа 7 перед объединением с `main`.
