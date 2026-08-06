# Этап 9B: финальная qualification геолокации

Статус: завершён 6 августа 2026 года.

Подэтап закрыл оставшийся release gate этапа 9:

- зафиксирован immutable audit модельного профиля;
- утверждён validation set v1 с digest и attribution;
- выполнены реальные Nominatim predictions по Москве, Санкт-Петербургу и Казани;
- пройдены extraction, model smoke, confidence, resolution, distance, kind и per-city gates;
- пройдены runtime-config и provider-policy gates;
- production registry создан только из полностью успешного report;
- predictions, report, registry и SHA-256 provenance сохранены в `evidence/geolocation/v1` и workflow artifact `8964191187`.

Публичный Nominatim используется только для контролируемой qualification. Production registry запрещает публичный endpoint и требует переключаемую собственную или договорную HTTPS-инфраструктуру.

Итоговый статус этапа приведён в `docs/STAGE_9_STATUS.md`. Следующий активный этап — этап 10.
