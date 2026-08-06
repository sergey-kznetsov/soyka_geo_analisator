# Geolocation qualification evidence v1

Qualification выполнена 6 августа 2026 года workflow run `31092771664` на PR merge ref `1a4ceeb59e3fa34837179cf0a12e20389b8311b5`.

## Итог

`approved_for_production=true`.

Production profile: `soika-geolocation-ru-v1`.

Подтверждённые уровни: `house`, `poi`, `landmark`.

Report digest: `0d68d8c102da8548703b0468e64f429cd9c9a8d0a210aa40674a55c441bbfd73`.

Registry digest: `6f3be8ddd720bce2b29183a44640dacccac24fd3a9146ec7c3c2c9605b586da9`.

Prediction digest: `141228c900845bc300ebfff79705cc06caf2adb83aeec05ccd5b0f39348d6bf2`.

Validation digest: `67a9573b285f0a8343f9e966fd1951b2fc1a9a3c5f36d8f72aae140b8d791685`.

## Метрики

- samples: 24;
- extraction exact rate: 0.958333;
- resolution rate: 0.958333;
- within-tolerance rate: 0.875;
- kind accuracy: 0.958333;
- low-confidence rate: 0;
- median distance: 119.782 м;
- p95 distance: 1 352.251 м.

## Workflow artifact

Artifact ID: `8964191187`.

Artifact ZIP SHA-256: `156eef59a79777b3ae3a17be25b45d60d6304b0f2f1de16323cbbce4c61c26a1`.

Файлы артефакта:

- `geolocation-qualification-report.json`: SHA-256 `3ef4517af9337fb98f752c56ae1deba8b0041695e4d2e74d24a743af296b73c3`;
- `geolocation-production-registry.json`: SHA-256 `8b9261dc8dfabb39ca0e000df17f73770ef7e8d973f739a92d320e3ca26b3e98`;
- `geolocation-predictions.json`: SHA-256 `7c75ac91fad781b7ac5e95b201d80b34561eb82ff86a68d167ad3a03abfcc30d`.

Report и production registry зафиксированы в этой директории. Полный predictions-файл сохранён в workflow artifact; выбранные результаты приведены в `SELECTED_PREDICTIONS.md`.

## Воспроизводимость

Qualification runtime установлен из `poetry.lock`. Workflow дополнительно проверил версии Natasha, Navec, Razdel, Slovnet, Yargy, pymorphy3 и requests, а также SHA-256 wheel Natasha 1.6.0.

Все gates прошли: model audit, model smoke, validation manifest, sample size, extraction quality, confidence policy, resolution quality, distance quality, kind quality, city quality, runtime config и provider policy.
