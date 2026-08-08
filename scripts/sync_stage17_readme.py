from __future__ import annotations

from pathlib import Path

MARKER = "## Release-candidate и разделение окружений (этапы 16–17)"
SECTION = """

## Release-candidate и разделение окружений (этапы 16–17)

Этап 16 добавил единый release-candidate gate: unit/contract/regression, module protocol, PostgreSQL/PostGIS, queue load/soak, failure injection, backup/restore, controlled-external/geolocation evidence, Bandit и dependency audit. Этап 17 устраняет найденные этим gate проблемы без ослабления проверок.

Production runtime и историческое research-окружение разделены. `pyproject.toml` и `poetry.lock` описывают только серверный runtime, который используется Docker image, CPU/GPU worker и `soika-module-api`. Импортированный исследовательский стек `factfinder` сохраняется отдельно в `requirements-legacy-research.txt` исключительно для изолированного воспроизведения старых экспериментов и не должен устанавливаться в production environment. Подробная граница зафиксирована в `docs/LEGACY_RESEARCH_ENVIRONMENT.md`.

Регистрация источника модуля в Geo Analyzer по HTTPS Git URL или пути на сервере по-прежнему является только регистрацией/проверкой происхождения: сервер автоматически не скачивает и не запускает непроверенный код. Runtime-интеграция СОЙКИ использует versioned module protocol `1.0.0` и private authenticated transport.

Security policy fail-closed: release candidate блокируется при известных уязвимостях production dependency lock или HIGH findings static scan. Legacy ML-модели не получают production approval из-за обновления библиотек или зелёных regression tests; обязательные model/training/validation/calibration/drift evidence остаются отдельным условием допуска.

Актуальные результаты испытаний и исправлений: `docs/STAGE_16_TESTING.md` и `docs/STAGE_17_STATUS.md`.
"""


def main() -> int:
    path = Path("README.md")
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return 0
    path.write_text(text.rstrip() + SECTION + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
