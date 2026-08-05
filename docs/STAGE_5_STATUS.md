# Этап 5: платформа парсеров

Статус: выполнен 5 августа 2026 года.

Область изменений ограничена репозиторием СОЙКА UDS Development. Geo Analyzer 2 и пользовательский интерфейс не изменялись.

Реализовано:

- backend-only интерфейс `ParserAdapter`;
- `ParserRegistry`;
- обязательный `SourcePolicy`;
- обязательное исследовательское досье источника;
- статусы permission review;
- permission evidence;
- сроки повторного review;
- fail-closed `ComplianceGate`;
- правила robots для public web;
- data minimization;
- HMAC pseudonymization author IDs;
- запрет специальных категорий, биометрии, детских данных и raw identifiers без отдельного release-gate;
- domain allowlist;
- SSRF protection;
- safe redirect handling;
- content-type и response-size limits;
- protected headers;
- secret references;
- безопасный transport, передаваемый адаптеру;
- token-bucket rate limit;
- retry только объявленных временных ошибок;
- pagination;
- atomic checkpoints;
- восстановление после restart;
- persistent deduplication external IDs по SHA-256;
- coverage;
- append-only audit;
- JSON Schema source policy;
- шаблон source policy;
- правовой и security checklist;
- fixture-based tests без реальной сети.

Конкретные источники не активированы. Их подключение относится к этапу 6 и допускается только после отдельного исследования условий доступа.

Критерий завершения подтверждён: новый источник подключается адаптером и policy manifest без изменения аналитического ядра, а неутверждённый источник блокируется до первого сетевого запроса.

Проверка:

- SHA-256 всех частей исходного пакета — успешно;
- Python 3.11 compilation — успешно;
- Ruff — успешно;
- 77 детерминированных unit-, contract-, orchestration- и parser-platform тестов — успешно;
- проверка `poetry.lock` — успешно;
- сборка и запуск CPU-контейнера — успешно;
- `/healthz` и `/readyz` — успешно;
- сборка GPU Docker target — успешно.

Платформа обеспечивает технические и организационные release-gates, но не заменяет юридическое заключение по конкретному источнику и юрисдикции.
