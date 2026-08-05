# Этап 7: preprocessing и дедупликация

Статус: выполнен 5 августа 2026 года.

Область изменений ограничена репозиторием СОЙКА UDS Development. Geo Analyzer 2 не изменялся.

Реализовано:

- неизменяемый контракт `PreprocessedMessage`;
- обязательное сохранение исходного текста `raw_text`;
- Unicode NFKC и удаление zero-width space;
- безопасная очистка HTML, `script`, `style` и HTML entities;
- нормализация пробелов и служебных строк;
- разделение текста автора и цитат;
- детерминированное определение русского, английского, смешанного и неизвестного языка;
- нормализация времени в UTC;
- SHA-256 очищенного текста и семантический fingerprint;
- exact duplicate detection;
- near-duplicate detection по token sequence и Jaccard similarity;
- временное окно, отделяющее технический дубль от повторного обращения;
- cross-source duplicate linkage к первому наблюдению;
- полная трассировка каждого преобразования с SHA-256 до и после;
- интеграционный fixture-набор для всех семи источников этапа 6B;
- итоговый fixture-отчёт `docs/STAGE_7_FIXTURE_RESULTS.json`;
- версия пакета `0.9.0`.

Новые runtime-зависимости не добавлены. Сеть, внешние модели и DataFrame для этапа не требуются.

## Проверка

- Python 3.11 compilation — success;
- Ruff — success;
- 124 deterministic unit, contract, orchestration, parser-platform, connector-adapter и preprocessing tests — success;
- fixture integration для семи источников — success;
- `poetry.lock` consistency — success;
- CPU Docker build и запуск — success;
- `/healthz` и `/readyz` — success;
- GPU Docker target build — success.

Критерий завершения выполнен: преобразования воспроизводимы, исходный текст сохраняется без потерь, а технические дубли отделяются от повторных обращений по настраиваемому временному окну.
