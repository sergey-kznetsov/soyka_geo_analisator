# External collection credentials

SOIKA never stores external credentials in the repository. GitHub-hosted deployment
uses repository Actions secrets and passes them to a manual preflight or deployment
workflow only when explicitly referenced.

## Required repository secrets

| Secret | Purpose | Production state |
| --- | --- | --- |
| `YANDEX_SEARCH_API_KEY` | Yandex Search API v2 authentication | required for RU web discovery |
| `YANDEX_SEARCH_FOLDER_ID` | Yandex Cloud folder used by Search API | required for RU web discovery |
| `TWO_GIS_API_KEY` | 2GIS Places API access | required for 2GIS POI/review statistics |
| `TELEGRAM_API_ID` | Telegram application ID | preparatory only |
| `TELEGRAM_API_HASH` | Telegram application hash | preparatory only |
| `TELEGRAM_STRING_SESSION` | authorized Telethon user session | preparatory only; highly sensitive |

OpenStreetMap/Overpass POI enrichment does not require an API key.

## GitHub repository location

Repository → **Settings** → **Secrets and variables** → **Actions** →
**Secrets** → **New repository secret**.

Never place secret values in repository variables, workflow YAML, `.env` committed to
Git, issues, pull requests, Actions artifacts, test fixtures, or logs.

## Yandex Search API

Use a dedicated Yandex Cloud service account in the folder that will own Search API
usage. Assign `search-api.webSearch.user`. Create an API key for the account with the
`yc.search-api.execute` scope. Store the API-key **secret value**, not the key ID, in
`YANDEX_SEARCH_API_KEY`. Store the folder ID in `YANDEX_SEARCH_FOLDER_ID`.

The Yandex Cloud console shows the API-key secret only when the key is created. If it
is lost or exposed, create a replacement and delete the old key.

SOIKA uses Search API v2 with the Russian search type. A separate Yandex Maps
Organization Search API key is not required by the current implementation and would
not provide public review texts.

## 2GIS Places API

Use 2GIS Platform Manager. A demo key can be created from the Dashboard for testing;
a production key is created under API Keys after purchasing the relevant subscription.
For server-side HTTP Places API use a normal API key, not a mobile-SDK-only key bound
to an App ID. Store the value in `TWO_GIS_API_KEY`.

The documented Places API exposes organization metadata and review statistics such as
average rating and review count. It does not expose review texts. SOIKA therefore
marks text-review collection as unavailable instead of scraping the consumer 2GIS UI.

## Telegram

`api_id` and `api_hash` are obtained from `my.telegram.org` → **API development tools**
after signing in with an active Telegram account and registering an application.
Telethon can export an authorized `StringSession`; that string is equivalent to a
login credential and must be protected like a password.

Current Telegram API and Content Licensing terms prohibit scraping/indexing/harvesting/
aggregation of platform data for AI/ML development or deployment. SOIKA therefore
keeps Telegram production collection fail-closed under `TERMS_RESTRICTED`. The
collector code and credential names remain prepared, but production activation must
not occur unless a documented Telegram-permitted exception applies.

## Manual credential preflight

After the Yandex and 2GIS secrets are installed, run GitHub Actions workflow
`collection-credentials-preflight` using **Run workflow**. It performs one bounded
Yandex Search request and one bounded 2GIS Places request and uploads a redacted JSON
report. The workflow is explicitly not a collection acceptance test.

Telegram credentials are never exercised by this preflight while the source is
policy-blocked.
