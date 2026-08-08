# Telegram collection

## Scope

Telegram is an active geo-first source class. Discovery remains territory-first: SOYKA resolves the input address into city/region/street/house, Yandex Search discovers relevant public `t.me` pages/channels, then the Telegram collector reads public channel history with an authenticated MTProto user session.

The Bot API is not used as the crawler. Telegram public channel history and comments are collected through a deployment-owned user session. The worker never performs interactive login.

## Credentials

Deployment provides three secret files:

- `SOIKA_TELEGRAM_API_ID_FILE`
- `SOIKA_TELEGRAM_API_HASH_FILE`
- `SOIKA_TELEGRAM_SESSION_FILE`

The session file contains an already-authorized Telethon `StringSession`. Secrets are never committed to the repository and must not be logged.

If any secret is missing, the collector returns `API_CREDENTIALS_MISSING` / `configuration_missing`. If the session exists but is no longer authorized, the collector returns `AUTH_REQUIRED`. SOYKA must not silently omit Telegram from coverage.

## Collection algorithm

For a Yandex-discovered public Telegram candidate:

1. parse the public username and optional message id from `t.me`/`telegram.me` URL;
2. reject invite/private links;
3. open the channel with MTProto;
4. if the candidate is a concrete post, fetch that post;
5. otherwise search the channel history using address-first terms generated from `GeoScope`;
6. for each selected broadcast-channel post, use Telethon `iter_messages(..., reply_to=post_id)` to read public comments from the linked discussion when available;
7. emit `SourceMessage` records with no retained author identifier;
8. preserve `telegram_post` / `telegram_comment` content type and downstream geo-filter requirement;
9. return explicit source coverage.

Search terms are bounded and address-first, for example:

```text
Пушкинская улица 277
Ижевск Пушкинская улица 277
Ижевск Пушкинская улица
Ижевск Пушкинская 277
```

A city-only mention is not treated as final house relevance. Per-message geolocation/spatial filtering remains authoritative.

## Error mapping

- Telegram flood wait -> `HTTP_429`, retryable, with wait seconds when available;
- unauthorized/revoked/private session/channel -> `AUTH_REQUIRED`;
- nonexistent username/peer -> `NO_RESULTS`;
- missing Telethon runtime/secrets -> `SOURCE_CONFIGURATION_MISSING` / `API_CREDENTIALS_MISSING`;
- other MTProto RPC/runtime failures -> `PARSER_FAILED`.

No flood-limit, authorization, private-channel or anti-abuse mechanism is bypassed.

## Dependency

Runtime is pinned to Telethon `1.44.0` and its required pure-Python dependencies in `requirements-telegram.txt` with SHA-256 hashes. The dependency is installed into the common worker venv; it is imported lazily by the gateway.

## Research basis

Before implementation, current Stack Overflow/Telethon and official Telegram material was reviewed. Telethon 1.44 documents server-side search within a chat using `iter_messages(..., search=...)` and public channel comments using `iter_messages(channel, reply_to=post_id)`. Current Telegram API also exposes `channels.searchPosts`, `messages.getHistory`, `messages.getDiscussionMessage` and `messages.getReplies` to user-authorized clients. This slice deliberately uses Yandex for channel/post discovery and Telethon history search for collection instead of depending on a Telegram-wide full-text method whose schema may vary by Telegram layer.

References:

- https://stackoverflow.com/questions/60507633/how-to-get-messages-of-the-public-channels-from-telegram
- https://stackoverflow.com/questions/46525921/read-the-messages-of-the-public-channels-from-telegram
- https://stackoverflow.com/questions/72396273/how-to-use-getrepliesrequest-call-in-telethon
- https://docs.telethon.dev/en/stable/modules/client.html
- https://docs.telethon.dev/en/stable/
- https://core.telegram.org/method/channels.searchPosts
- https://core.telegram.org/method/messages.getHistory
- https://core.telegram.org/method/messages.getDiscussionMessage
- https://core.telegram.org/method/messages.getReplies

## Acceptance

This code slice is not live acceptance by itself. Telegram collection is production-demonstrated only when deployment credentials are supplied and the real address acceptance test returns actual public Telegram posts/comments with `t.me` URLs and source coverage. Fixture/mock messages do not count.
