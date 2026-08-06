# Этап 9B: финальная qualification геолокации

Статус: выполняется qualification на зафиксированном target-city validation set.

Подэтап закрывает оставшийся release gate этапа 9:

- immutable audit модельного профиля;
- validation set v1 с digest и attribution;
- реальные Nominatim predictions по Москве, Санкт-Петербургу и Казани;
- extraction, confidence, resolution, distance, kind и per-city gates;
- policy gate внешнего провайдера;
- production registry, создаваемый только из полностью успешного report;
- сохранённые predictions, report и registry с SHA-256 provenance.

Публичный Nominatim используется только для контролируемой qualification. Production registry запрещает публичный endpoint и требует переключаемую собственную или договорную инфраструктуру.

После успешного workflow report и registry фиксируются в репозитории, `docs/STAGE_9_STATUS.md` переводится в состояние «завершён», а этап 10 становится следующим активным этапом.
