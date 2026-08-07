# Связи, показатели и риск

Пакет `soika_uds.scoring` реализует этап `PipelineStage.SCORING` после формирования событий. Контур заменяет legacy-логику связей и риска отдельным воспроизводимым слоем и не изменяет Geo Analyzer 2.

## Вход

Scoring stage использует два подтверждённых checkpoint:

- `events.events.events` — события этапа 11;
- `filtering.spatial_filtering.results` — точки сообщений для геометрии и метрических показателей.

События не перекластеризуются. Этап 12 работает только с наблюдаемыми результатами предыдущих стадий.

## Связи между событиями

Связь существует только при непустом точном пересечении множеств `message_ids`. Идентификаторы рассматриваются как целые строки, не как последовательности символов.

Для связи сохраняются canonical event IDs, `shared_message_ids`, Jaccard index, тип связи, category/topic coincidence, temporal gap, GeoJSON geometry, исходная CRS, использованная метрическая CRS и `distance_m`.

Расстояние основывается на WGS84 geodesic. Для локальных рёбер дополнительно используется локально центрированная Azimuthal Equidistant (`+proj=aeqd`) CRS через `pyproj.Transformer(..., always_xy=True)`. Для географически длинных рёбер остаётся WGS84 geodesic и `metric_crs=null`, поэтому одна локальная проекция не применяется глобально.

При пересечении антимеридиана GeoJSON не записывается как один `LineString` от `179.x` к `-179.x`. Ребро разрезается на границе `+180/-180` и сериализуется как `MultiLineString`, поэтому визуальная геометрия соответствует короткому geodesic distance, а не проходит через всю карту.

## Spatial spread

Для набора точек сначала вычисляется spherical centroid и WGS84 geodesic расстояния до него.

Если максимальный радиус не превышает 1 500 км, spread уточняется в локальной AEQD CRS. Это одинаково работает в обычных широтах, у полюсов и у антимеридиана.

Если набор географически широкий, локальная проекция не используется: `spatial_spread` остаётся WGS84 geodesic, а `event_metric_crs` равен `null`. Поэтому валидные глобальные наборы, включая точки около `0°/180°`, не проходят через одну неподходящую UTM-зону и не дают non-finite координаты.

## Наблюдаемые показатели

Baseline 1.0.0 использует четыре показателя. Каждый хранится отдельно как `raw_value`, `reference_value`, `normalized_value`, `weight`, `contribution`, `status` и `reason`.

- `intensity` — число уникальных сообщений события;
- `persistence` — длительность между `started_at` и `ended_at` в часах;
- `connectivity` — число уникальных внешних сообщений через связанные события;
- `spatial_spread` — метрический географический разброс сообщений события.

Nested building/link/road/global events не удваивают connectivity: внешние `message_ids` объединяются множеством.

Legacy `population` и category `importance` не входят в baseline 1.0.0, поскольку текущий production-контракт не предоставляет квалифицированного population evidence, а legacy category weights не имеют подтверждённой экспертной валидации.

## Нормализация и формула

Dataset-relative min-max не используется. Для каждого показателя применяется:

`normalized = min(1, raw_value / fixed_positive_reference)`

Baseline references:

- intensity — 20 сообщений;
- persistence — 168 часов;
- connectivity — 20 уникальных внешних сообщений;
- spatial spread — 2000 метров.

Baseline weights равны `0.25` для каждого показателя. Формула:

`score = Σ(weight_i × normalized_i)`

`RiskScoringConfig` допускает только машинную погрешность суммы весов около 1.0. Перед расчётом contributions веса детерминированно переводятся в effective unit budget через монотонно уменьшающийся `remaining`. Поэтому floating-point residual не может сделать сумму эффективных весов больше 1, а score остаётся согласованным с contributions и ограниченным `[0, 1]`.

Bands:

- `low`: score < 0.25;
- `medium`: 0.25 ≤ score < 0.50;
- `high`: 0.50 ≤ score < 0.75;
- `critical`: score ≥ 0.75;
- `unavailable`: хотя бы одно обязательное наблюдение отсутствует.

Эти references, thresholds и weights являются техническим baseline, а не доказанными социальными или управленческими порогами.

## Отсутствующие данные

Отсутствие данных не преобразуется в ноль. Если нет временных границ либо полного набора точек, соответствующий indicator получает `status=missing`, а итоговый `score=null`, `band=unavailable`.

## Экспертная валидация

`ExpertValidationManifest` связывает экспертный акт с `formula_version`, `config_digest`, `review_id`, ролью, датой, SHA-256 evidence и исходным решением `approved`.

Manifest сам по себе не разрешает decision-use. `decision_use_approved=true` возможно только при одновременном выполнении условий:

- manifest имеет `approved=true`;
- `formula_version` и `config_digest` совпадают с текущей конфигурацией;
- внешний `expert_validation_verifier` настроен;
- verifier подтвердил evidence.

`formula_validation.approved` отражает только эффективный текущий gate. Исходное решение хранится отдельно в `manifest_approved`, поэтому stale manifest не может выглядеть применимым.

До реального внешнего expert evidence `decision_use_approved=false`, а handler выдаёт `RISK_FORMULA_NOT_EXPERT_VALIDATED`.

## Воспроизводимость

Public input сначала валидируется, затем сортируется. Events сортируются по `event_id`, рёбра — по event IDs, наборы сообщений — по полным identifiers. `message_points` требуют непустых строковых ключей. Результат содержит schema/algorithm/formula versions, input/config/output SHA-256 digests, formula-validation metadata и provenance spatial/weight policies.

Один и тот же набор событий и точек даёт одинаковый результат независимо от порядка входных коллекций.

## Regression gates

Тесты блокируют:

- посимвольное пересечение строковых `message_ids`;
- фиктивное назначение CRS без coordinate transform;
- неверный центр у антимеридиана;
- несогласованный `LineString` через ±180°;
- Web Mercator/неподходящую локальную проекцию у полюсов;
- одну локальную проекцию для глобально широкого события;
- dataset-relative min-max и zero-range division;
- score overflow из-за tolerance/residual floating-point весов;
- двойной учёт nested events;
- трактовку missing observation как нулевого риска;
- stale expert approval и decision-use без evidence verifier;
- чтение `event_id` у malformed input до проверки типа;
- зависимость результата от порядка входа.

## Граница этапа

Stage 12 формирует связи и технический risk score. Хранение событий, связей и risk history в PostgreSQL/PostGIS относится к этапу 13.
