# Transport СОЙКА ↔ Geo Analyzer — этап 15

Этот документ фиксирует приватный server-to-server контракт СОЙКИ как подключаемого аналитического модуля Geo Analyzer.

## Граница систем

Git-ссылка или путь на сервере являются только источником поставки модуля. Регистрация источника в Geo Analyzer не означает скачивание, импорт или запуск кода. Runtime-взаимодействие начинается только после отдельной проверки и разрешения модуля.

СОЙКА не предоставляет пользовательский интерфейс. Checkbox запуска, состояние функции в администрировании и отображение дополнительной вкладки принадлежат Geo Analyzer. СОЙКА только декларирует, что поддерживает эти точки интерфейса.

## Версия протокола

Текущая версия универсального module protocol: `1.0.0`.

Идентификатор СОЙКИ в реестре модулей: `soyka.reviews`.

Версия протокола отделена от версии пакета СОЙКИ. Несовместимое изменение transport contract требует новой major-версии протокола, а не молчаливого изменения существующего формата.

## Manifest

`GET /v1/manifest` возвращает описание возможностей модуля. Для СОЙКИ manifest сообщает:

- `module_id = soyka.reviews`;
- операции submit/status/cancel/retry/result;
- форматы JSON и GeoJSON;
- поддержку partial result и warnings;
- `ui.optional = true`;
- `ui.default_enabled = false`;
- `ui.analysis_launch_toggle = true` — закладка для будущего checkbox на экране запуска исследования;
- `ui.capability_card = true` — закладка для будущего включения/отключения модуля в блоке «Возможности».

Эти поля являются декларацией. СОЙКА не рисует UI и не управляет состоянием кнопок Geo Analyzer.

## HTTP API

Приватный HTTP transport использует следующие операции:

```text
GET  /v1/manifest
GET  /v1/health
POST /v1/analyses
GET  /v1/analyses/{analysis_id}
GET  /v1/analyses/{analysis_id}/result
POST /v1/analyses/{analysis_id}/cancel
POST /v1/analyses/{analysis_id}/retry
```

Все операции требуют `Authorization: Bearer <token>`.

Сервер по умолчанию слушает только loopback. Привязка к другому адресу требует явного `--allow-remote`; в production доступ должен дополнительно ограничиваться приватной сетью Geo Analyzer и правилами инфраструктуры.

Bearer token и DSN базы данных читаются только из secret files. Передача этих секретов через argv не поддерживается.

## Входное задание

Общий envelope Geo Analyzer:

```json
{
  "protocol_version": "1.0.0",
  "module_id": "soyka.reviews",
  "analysis_id": "geo-123-soyka",
  "requested_at": "2026-08-08T08:00:00Z",
  "idempotency_key": "geo-analyzer:...",
  "territory": {
    "city": "Ижевск",
    "address": "Ижевск, Пушкинская, 277",
    "point": {
      "latitude": 56.8526,
      "longitude": 53.2115
    },
    "radius_meters": 1500
  },
  "sources": [],
  "options": {},
  "allow_partial": true
}
```

Transport adapter преобразует этот envelope во внутренний `AnalysisRequestV1`. Внутренний orchestration/worker contract этапа 14 не раскрывается Geo Analyzer.

## Статусы

Публичный module protocol использует компактные состояния:

```text
queued
running
completed
completed_with_warnings
failed
cancelled
```

В поле `raw_status` СОЙКА может вернуть более подробную внутреннюю стадию. Geo Analyzer не должен строить бизнес-логику на `raw_status`; стабильным является публичное поле `status`.

## Результат

Результат содержит универсальный envelope:

```json
{
  "protocol_version": "1.0.0",
  "module_id": "soyka.reviews",
  "module_version": "0.20.0",
  "analysis_id": "geo-123-soyka",
  "status": "completed",
  "generated_at": "2026-08-08T08:05:00Z",
  "partial": false,
  "coverage": {},
  "warnings": [],
  "errors": [],
  "result": {},
  "geojson": {
    "type": "FeatureCollection",
    "features": []
  },
  "report_sections": []
}
```

`result` остаётся модульно-специфичным payload. Поля envelope стабильны для универсального коннектора Geo Analyzer.

`report_sections` содержит табличные секции, которые Geo Analyzer может безопасно добавить в общий Excel после завершения собственного отчёта. СОЙКА не открывает и не изменяет основной файл Geo Analyzer напрямую.

## Ошибки

HTTP-ошибки возвращаются как `application/problem+json` в стиле RFC 9457. Клиент не должен интерпретировать текст исключения как контракт; для принятия решений используются HTTP status и поля module envelope.

Основной отчёт Geo Analyzer не зависит от успешности СОЙКИ. Ошибка transport, timeout, `failed`, `cancelled` или отказ от partial result должны фиксироваться как состояние дополнительного модуля, а не ломать основной анализ.

## Запуск transport процесса

```bash
export GEOANALYZER_DATABASE_DSN_FILE=/run/secrets/geoanalyzer_database_dsn
export SOIKA_MODULE_AUTH_TOKEN_FILE=/run/secrets/soika_module_token
python -m soika_uds.transport --host 127.0.0.1 --port 9080
```

Для production transport должен запускаться рядом с worker runtime и использовать ту же durable PostgreSQL state, но отдельный process boundary.

## Совместимость

Geo Analyzer должен зависеть только от module protocol `1.0.0`, а не от Python-пакета СОЙКИ. Это позволяет обновлять СОЙКУ независимо и использовать тот же connector для других аналитических модулей.
