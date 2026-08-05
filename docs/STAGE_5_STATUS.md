# Этап 5: платформа парсеров

Статус: реализован; выполняется финальная проверка полного репозитория.

Область изменений ограничена репозиторием СОЙКА UDS Development. Geo Analyzer 2 и пользовательский интерфейс не изменяются.

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

Критерий завершения: новый источник подключается адаптером и policy manifest без изменения аналитического ядра, а неутверждённый источник блокируется до первого сетевого запроса.

Промежуточная проверка платформы: SHA-256 всех частей исходного пакета подтверждён, Ruff применён, 23 изолированных parser-platform теста прошли.
