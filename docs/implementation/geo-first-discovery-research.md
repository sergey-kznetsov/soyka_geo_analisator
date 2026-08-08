# Research basis: geo-first discovery

Дата: 2026-08-08.

Перед реализацией были проверены типовые решения и официальные интерфейсы.

## HTTP vs browser

Stack Overflow discussions on `requests` vs browser automation confirm the architectural split used here: HTTP clients return the server response and do not execute page JavaScript; browser automation is needed only for dynamic DOM. Playwright guidance favors locator/auto-wait behavior over arbitrary sleeps. Anti-bot/headless detection is treated as an access failure, not something the program should bypass.

References:

- https://stackoverflow.com/questions/57249863/what-is-difference-between-soup-of-selenium-and-requests
- https://stackoverflow.com/questions/67536434/playwright-python-how-to-check-if-an-element-is-hidden

## Telegram

Community examples were reviewed together with current Telegram documentation. Current official API documentation is authoritative where old Stack Overflow answers differ.

References:

- https://stackoverflow.com/questions/60507633/how-to-get-messages-of-the-public-channels-from-telegram
- https://stackoverflow.com/questions/46525921/read-the-messages-of-the-public-channels-from-telegram
- https://stackoverflow.com/questions/72396273/how-to-use-getrepliesrequest-call-in-telethon
- https://core.telegram.org/method/channels.searchPosts
- https://core.telegram.org/method/messages.getHistory
- https://core.telegram.org/method/messages.getReplies

## Yandex Search

Yandex Search API v2 is used as the primary RU search-provider design instead of scraping the Yandex search web UI. Official documentation confirms synchronous endpoint `POST https://searchapi.api.cloud.yandex.net/v2/web/search`, `SEARCH_TYPE_RU`, API-key authentication, and Base64 `rawData` containing XML/HTML search output.

References:

- https://aistudio.yandex.ru/docs/ru/search-api/quickstart/
- https://aistudio.yandex.ru/docs/ru/search-api/api-ref/WebSearch/search.html
- https://aistudio.yandex.ru/docs/ru/search-api/concepts/web-search.html

## Engineering constraints

The implementation preserves the existing parser platform fail-closed rules and applies KISS/SOLID separation:

- territory resolution does not perform source collection;
- query building does not perform network IO;
- search-provider transport is injected and testable;
- source classification is independent of collection adapters;
- source status and failure reason are part of the contract;
- legacy VK/OK/RUTUBE adapters are not deleted solely to satisfy the new active perimeter, avoiding regressions in historical contracts and evidence.
