# Geo-first discovery российских источников

## Цель

СОЙКА не должна начинать сбор с заранее заданных URL или идентификаторов площадок. Сначала программа разрешает входной адрес до фактической географии, затем формирует локальное информационное пространство этой территории и только после этого запускает сбор.

Целевой порядок:

```text
address
  -> qualified geolocation
  -> city / region / district / street / house / point
  -> Russian search queries
  -> local source discovery
  -> source-specific collection
  -> explicit source coverage
  -> preprocessing / NLP / message geolocation / spatial filtering
```

Совпадение только по названию города не является достаточной причиной включать сообщение в итоговую статистику. Discovery ограничивает область поиска, а существующие стадии geolocation/filtering должны подтвердить связь каждого собранного сообщения с домом, улицей, радиусом, районом или другой заданной территорией.

## Активный периметр

В geo-first collection активны следующие классы:

- локальные и региональные СМИ;
- муниципальные и государственные публичные порталы;
- локальные городские сайты и форумы;
- публичные Telegram-каналы и, после подключения MTProto gateway, доступные обсуждения;
- Pikabu;
- Дзен;
- Яндекс Карты: карточки организаций и публичные отзывы при разрешённом способе доступа;
- 2ГИС: карточки организаций и публичные отзывы при разрешённом способе доступа;
- прочие локальные публичные web-источники, обнаруженные поиском и прошедшие source review.

Не входят в активный периметр текущего этапа:

- VK;
- Одноклассники;
- MAX;
- RUTUBE.

Legacy-код этих адаптеров может сохраняться для совместимости исторических тестов, но geo-first discovery не генерирует для них запросы и не передаёт их в активный сбор.

OpenStreetMap не считается площадкой отзывов. OSM используется для геокодирования, POI/entity enrichment и расширения поисковых сущностей.

## Разрешение территории до collection

`TerritoryResolver` получает исходный `TerritoryContext`, но не доверяет полю `city` как окончательной географии. При наличии адреса он прогоняет адрес через квалифицированный geolocation engine и строит `GeoScope` из выбранного кандидата:

- `city`;
- `region`;
- `district`;
- `street`;
- `house_number`;
- координаты;
- уровень точности;
- confidence;
- OSM id/type;
- исходный структурированный candidate address;
- признак совпадения переданного city hint с реально разрешённым городом.

Для house-level input street-only candidate считается недостаточным. Неразрешённая территория останавливает preparing stage до поиска источников.

Coordinate-only discovery требует отдельного reverse-geocoding production profile и в текущей реализации fail-closed.

## Поисковый провайдер

Основной провайдер для российского контура — Yandex Search API v2 с `SEARCH_TYPE_RU`.

Официальный synchronous endpoint:

```text
POST https://searchapi.api.cloud.yandex.net/v2/web/search
```

Авторизация выполняется API key через `Authorization: Api-Key ...`. Ответ запрашивается в `FORMAT_XML`, находится в поле `rawData` в Base64 и разбирается безопасным XML parser. API key и folder id не должны храниться в репозитории или попадать в audit/log output.

Реализация оставляет `SearchProvider` интерфейс независимым от Яндекса, чтобы резервный провайдер можно было добавить без изменения Discovery Engine.

Если credentials отсутствуют или API недоступен, это не трактуется как пустая выдача. В coverage создаётся явный outcome с кодом причины.

Официальные материалы:

- https://aistudio.yandex.ru/docs/ru/search-api/quickstart/
- https://aistudio.yandex.ru/docs/ru/search-api/api-ref/WebSearch/search.html
- https://aistudio.yandex.ru/docs/ru/search-api/concepts/web-search.html

## Запросы

`GeoQueryBuilder` строит ограниченный детерминированный набор запросов. Каждый запрос содержит город или регион. Используются, в частности:

```text
"Пушкинская улица 277" Ижевск
"Пушкинская улица 277" Ижевск отзывы
"Пушкинская улица 277" Ижевск новости
Ижевск новости городские СМИ
Ижевск администрация новости портал
Ижевск городской форум жители
site:t.me Ижевск Пушкинская улица 277
site:pikabu.ru Ижевск Пушкинская улица 277
site:dzen.ru Ижевск Пушкинская улица 277
site:yandex.ru/maps Ижевск Пушкинская улица 277
site:2gis.ru Ижевск Пушкинская улица 277
```

В запросы не включаются RUTUBE, VK, OK и MAX.

## Browser strategy

Headless browser не является поисковиком и не должен использоваться для имитации ручного поиска в Яндексе. Поиск выполняется официальным Search API.

Для найденной страницы порядок доступа должен быть таким:

1. официальный API / export, если предусмотрен источником;
2. RSS / Atom / sitemap;
3. безопасный обычный HTTP;
4. headless Chromium/Playwright только если нужный публичный контент появляется после JavaScript и такой способ разрешён source policy.

Browser adapter не должен обходить CAPTCHA, authentication wall, anti-bot challenge, robots decision или иные ограничения. Такие состояния фиксируются как недоступность с причиной.

Практика соответствует распространённой схеме requests-first/browser-fallback: обычный HTTP не исполняет JavaScript, а browser используется только для динамического DOM. Для Playwright следует использовать BrowserContext и locator/auto-wait вместо произвольных sleep.

## Telegram

Telegram должен подключаться не Bot API crawler-ом, а отдельным MTProto user-client gateway/service account, потому что официальные методы глобального поиска публичных постов и чтения истории/обсуждений предназначены для user authorization.

Целевые методы Telegram API:

- `channels.searchPosts` — global full-text search публичных channel posts;
- `messages.getHistory` — история конкретного публичного peer/channel;
- `messages.getDiscussionMessage` / `messages.getReplies` — discussion/comments where available.

Нужно учитывать search flood/quota/Premium/Stars ограничения Telegram и возвращать их как coverage, а не скрывать.

Официальные материалы:

- https://core.telegram.org/method/channels.searchPosts
- https://core.telegram.org/method/messages.getHistory
- https://core.telegram.org/method/messages.getDiscussionMessage
- https://core.telegram.org/method/messages.getReplies

Telegram gateway не реализуется через скрытые endpoints и не обходит auth/anti-abuse controls.

## Карты

### Яндекс Карты

Карточки организаций и публичные отзывы относятся к `yandex_maps`. Геокодирование/search API и review collection рассматриваются как разные возможности. Если разрешённый интерфейс не предоставляет тексты отзывов, СОЙКА обязана показать partial/unavailable outcome, а не считать источник полностью собранным.

### 2ГИС

Карточки организаций относятся к `two_gis`. Rating/review-count metadata не эквивалентны текстам отзывов. Отсутствие доступного разрешённого способа получить review text фиксируется явно.

### OpenStreetMap

OSM используется как `osm_entity` enrichment: здания, POI, организации, адреса и координаты. Найденные названия объектов могут расширять Yandex discovery queries. OSM не включается в счётчик review sources.

## Source outcome

Каждый discovered/attempted source обязан иметь status и объяснение. Основные состояния:

- `collected`;
- `partial`;
- `no_relevant_results`;
- `unavailable`;
- `blocked`;
- `auth_required`;
- `configuration_missing`;
- `failed`.

Причина записывается одновременно машинным кодом и человекочитаемым текстом. Поддерживаемые коды включают:

- `HTTP_403`;
- `HTTP_429`;
- `CAPTCHA`;
- `AUTH_REQUIRED`;
- `API_CREDENTIALS_MISSING`;
- `ROBOTS_DENIED`;
- `SOURCE_TIMEOUT`;
- `DNS_ERROR`;
- `SSL_ERROR`;
- `ANTI_BOT`;
- `PARSER_FAILED`;
- `NO_RELEVANT_CONTENT`;
- `NO_RESULTS`;
- `UNSUPPORTED_PAGE`;
- `SOURCE_CONFIGURATION_MISSING`;
- `SEARCH_PROVIDER_UNAVAILABLE`;
- `SOURCE_OUT_OF_SCOPE`;
- `TERRITORY_UNRESOLVED`.

`unavailable` и `no_relevant_results` принципиально различаются. Второе означает, что источник был успешно доступен и проверен, но подходящих данных не найдено.

## Coverage contract

Collection output сохраняет `messages` для совместимости с preprocessing и отдельный `source_coverage`.

Минимальная сводка:

```json
{
  "sources_discovered": 42,
  "sources_collected": 31,
  "sources_unavailable": 6,
  "sources_no_relevant_results": 5,
  "messages_collected": 128,
  "messages_relevant": 37
}
```

HTTP 200, Search API hit или metadata-only response не считаются собранным сообщением.

## Acceptance criterion

Функция source collection считается работающей только при боевом test case, где СОЙКА:

1. получает реальный адрес;
2. сама разрешает город/регион/координаты до collection;
3. через Yandex discovery находит реальные локальные источники;
4. реально читает разрешённые источники;
5. возвращает реальные `SourceMessage` с URL;
6. отдельно показывает каждый недоступный источник и причину;
7. отличает проверенный пустой источник от недоступного;
8. после message geolocation/spatial filtering показывает, какие сообщения действительно относятся к заданной территории.

Fixture/mock/HTTP reachability не подтверждают этот критерий.

Текущий implementation slice вводит geo-first preparing handler, RU Yandex Search API provider, query builder, source classification/perimeter и explicit collection coverage contracts. Конкретные browser, Telegram и map-review collectors требуют отдельных source-policy/credential/runtime implementation и не считаются готовыми до live acceptance.
