from __future__ import annotations

from soika_uds.classification import ModelDescriptor, TransformersPredictionBackend

MODEL_COMMIT = "a" * 40
TOKENIZER_COMMIT = "b" * 40
WEIGHTS_SHA = "c" * 64


def descriptor(tokenizer_id: str) -> ModelDescriptor:
    return ModelDescriptor(
        name="category",
        repo_id="example/category",
        revision=MODEL_COMMIT,
        license="MIT",
        task="category",
        tokenizer_id=tokenizer_id,
        tokenizer_revision=TOKENIZER_COMMIT,
        weights_sha256=WEIGHTS_SHA,
        approved_for_production=True,
        training_data_review="approved",
        label_space=("roads",),
    )


def test_pipeline_cache_key_includes_tokenizer_identity() -> None:
    pipelines: list[dict[str, object]] = []

    def tokenizer_factory(*args, **kwargs):
        return {"args": args, "kwargs": kwargs}

    def pipeline_factory(*args, **kwargs):
        pipelines.append(kwargs)

        def classify(texts, **call_kwargs):
            return [[{"label": "roads", "score": 1.0}] for _ in texts]

        return classify

    backend = TransformersPredictionBackend(
        pipeline_factory=pipeline_factory,
        tokenizer_factory=tokenizer_factory,
    )
    backend.predict_batch(
        ["one"],
        model=descriptor("example/tokenizer-one"),
        batch_size=1,
        device="cpu",
    )
    backend.predict_batch(
        ["two"],
        model=descriptor("example/tokenizer-two"),
        batch_size=1,
        device="cpu",
    )

    assert len(pipelines) == 2
    assert pipelines[0]["model_kwargs"] == {"local_files_only": True}


def test_artifact_verifier_runs_before_pipeline_creation() -> None:
    verified: list[str] = []

    def verifier(model: ModelDescriptor) -> None:
        verified.append(model.weights_sha256)

    def tokenizer_factory(*args, **kwargs):
        return object()

    def pipeline_factory(*args, **kwargs):
        return lambda texts, **call_kwargs: [
            [{"label": "roads", "score": 1.0}] for _ in texts
        ]

    backend = TransformersPredictionBackend(
        pipeline_factory=pipeline_factory,
        tokenizer_factory=tokenizer_factory,
        artifact_verifier=verifier,
    )
    backend.predict_batch(
        ["one"],
        model=descriptor("example/tokenizer"),
        batch_size=1,
        device="cpu",
    )

    assert verified == [WEIGHTS_SHA]
