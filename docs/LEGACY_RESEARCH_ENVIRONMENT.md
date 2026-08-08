# Legacy research environment

`factfinder` is preserved in this repository as imported research code and evidence of the original SOIKA experiments. It is **not** the production runtime boundary.

Stage 16 dependency auditing showed that the historical dependency graph contains known security advisories and conflicts with the current production ML stack. Keeping those packages in an optional Poetry group does not isolate them: Poetry resolves all groups into one lock file, so legacy Flair/Transformers constraints can prevent a safe production lock from being generated.

Stage 17 therefore separates the environments:

- `pyproject.toml` / `poetry.lock` describe the server-side production runtime used by Docker images, workers and `soika-module-api`;
- `requirements-legacy-research.txt` preserves the historical direct versions for isolated reproduction of old research code only;
- production CI and `pip-audit` do not install the legacy research file;
- legacy research dependencies must never be installed into the production worker/module API environment;
- no legacy model receives production approval merely because its historical environment can be reconstructed.

The normalized server code may continue to reuse immutable data files imported with `factfinder`, but execution of legacy research modules is outside the production contract. If a legacy algorithm is promoted later, its dependencies must first be moved into the production profile explicitly and pass the full release-candidate, security and model-qualification gates.

## Model boundary

`LocalFlairAddressExtractor` remains a lazy adapter. The production geolocation factory receives a `MentionExtractor` through its interface and does not require Flair. A deployment that intentionally chooses the legacy Flair extractor must provide a separately qualified compatible runtime; the default production dependency set does not install Flair.

The Transformers classification backend remains in the production dependency graph because normalized classification code uses it directly. Its library upgrade does not approve any model artifact. Model revision, weights, validation, calibration and drift evidence remain fail-closed requirements.
