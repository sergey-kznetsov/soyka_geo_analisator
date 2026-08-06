# Этап 10: пространственная фильтрация территории

Статус: реализация завершена в ветке, финальная CI-проверка выполняется.

Geo Analyzer 2 не изменяется.

## Реализовано

- отдельный пакет `soika_uds.spatial_filtering`;
- durable `PipelineStage.FILTERING` между геолокацией и событиями;
- радиус, Polygon, MultiPolygon и пересечение ограничений;
- включение пограничных точек;
- локальная метрическая CRS для расстояний и polygon predicates;
- RFC 7946/WGS84 validation и запрет неоднозначного `crs` member;
- отдельные `included`, `excluded`, `indeterminate` и `skipped` решения;
- fail-closed обработка отсутствующей и неточной геометрии;
- причины каждого решения и CRS/candidate provenance;
- input, target, config и output SHA-256;
- независимость результата от порядка входа;
- миграционно-готовый PostGIS GiST/index и query plan;
- unit и orchestration tests.

## Критерий завершения

Критерий считается подтверждённым после успешных compile, Ruff, полного unit suite, lock consistency, CPU Docker health/readiness и GPU target build.

Подробности: `docs/SPATIAL_FILTERING.md`.
