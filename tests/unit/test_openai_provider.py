"""El proveedor OpenAI se prueba con un cliente sustituto: CI no llama a la API real."""

from types import SimpleNamespace

import openai
import pytest

from app.core.config import Settings
from app.integrations.ai.base import AgentRequest, AIError, SafetyClassification, Turn
from app.integrations.ai.openai_provider import (
    OpenAIProvider,
    _WireDraft,
    _WireMemory,
    _WireProposal,
)


class StubClient:
    def __init__(self) -> None:
        self.calls: dict[str, dict] = {}
        self.parsed: object = None
        self.error: Exception | None = None
        self.responses = SimpleNamespace(parse=self._record("responses.parse"))
        self.embeddings = SimpleNamespace(create=self._record("embeddings.create"))
        self.moderations = SimpleNamespace(create=self._record("moderations.create"))
        self.audio = SimpleNamespace(
            transcriptions=SimpleNamespace(create=self._record("transcriptions.create")),
            speech=SimpleNamespace(create=self._record("speech.create")),
        )
        self.results: dict[str, object] = {}

    def _record(self, name: str):
        def call(**kwargs: object) -> object:
            self.calls[name] = kwargs
            if self.error is not None:
                raise self.error
            return self.results[name]

        return call


@pytest.fixture
def stub() -> StubClient:
    return StubClient()


@pytest.fixture
def provider(stub: StubClient) -> OpenAIProvider:
    return OpenAIProvider(Settings(), client=stub)


def request() -> AgentRequest:
    return AgentRequest(
        instructions="INSTRUCCIONES",
        context_text="<datos_recuperados>dato</datos_recuperados>",
        message="Hola",
        turns=(Turn("user", "antes"), Turn("assistant", "respuesta")),
    )


def test_generate_keeps_instructions_apart_from_data(provider, stub) -> None:
    stub.results["responses.parse"] = SimpleNamespace(
        output_parsed=_WireDraft(
            reply=" Hola. ", proposals=[], memory_candidates=[], used_source_ids=["a"]
        )
    )

    draft = provider.generate(request())

    call = stub.calls["responses.parse"]
    assert call["model"] == "gpt-4.1-mini"
    assert call["instructions"] == "INSTRUCCIONES"
    assert [item["role"] for item in call["input"]] == ["user", "assistant", "user"]
    assert "<datos_recuperados>" in call["input"][-1]["content"]
    assert "<datos_recuperados>" not in call["instructions"]
    assert call["store"] is False
    assert draft.reply == "Hola." and draft.used_source_ids == ["a"]


def test_generate_drops_items_outside_the_schema(provider, stub) -> None:
    stub.results["responses.parse"] = SimpleNamespace(
        output_parsed=_WireDraft(
            reply="Hola",
            proposals=[
                _WireProposal(
                    kind="enviar_ya", title="x", body="y", activity_id=None, contact_id=None
                ),
                _WireProposal(
                    kind="activity", title="x", body="y", activity_id=None, contact_id=None
                ),
            ],
            memory_candidates=[
                _WireMemory(category="diagnosis", content="x"),
                _WireMemory(category="interest", content="Le gusta el jazz"),
            ],
            used_source_ids=[],
        )
    )

    draft = provider.generate(request())

    assert [p.kind for p in draft.proposals] == ["activity"]
    assert [m.category for m in draft.memory_candidates] == ["interest"]


def test_generate_without_structured_output_is_an_error(provider, stub) -> None:
    stub.results["responses.parse"] = SimpleNamespace(output_parsed=None)

    with pytest.raises(AIError):
        provider.generate(request())


def test_provider_errors_become_ai_errors(provider, stub) -> None:
    stub.error = openai.OpenAIError("sin conexión")

    for call in (
        lambda: provider.generate(request()),
        lambda: provider.embed(["hola"]),
        lambda: provider.moderate("hola"),
        lambda: provider.classify_safety(message="hola", recent_turns=[]),
        lambda: provider.transcribe(audio=b"x", filename="a.wav", language="es"),
        lambda: provider.synthesize("hola"),
    ):
        with pytest.raises(AIError):
            call()


def test_moderation_keeps_category_flags_not_scores(provider, stub) -> None:
    stub.results["moderations.create"] = SimpleNamespace(
        results=[
            SimpleNamespace(
                flagged=True,
                categories=SimpleNamespace(
                    self_harm=True, self_harm_intent=False, self_harm_instructions=False
                ),
                category_scores=SimpleNamespace(self_harm=0.93),
            )
        ]
    )

    result = provider.moderate("texto")

    assert stub.calls["moderations.create"]["model"] == "omni-moderation-latest"
    assert result.self_harm is True and result.self_harm_intent is False
    assert not hasattr(result, "category_scores")


def test_classifier_uses_structured_output_and_versions_itself(provider, stub) -> None:
    expected = SafetyClassification(
        subject="self", timeframe="present", negated=False, intent="none", bypass_attempt=False
    )
    stub.results["responses.parse"] = SimpleNamespace(output_parsed=expected)

    result = provider.classify_safety(message="Hola", recent_turns=[Turn("user", "antes")])

    call = stub.calls["responses.parse"]
    assert result == expected
    assert call["text_format"] is SafetyClassification and call["temperature"] == 0
    assert "antes" in call["input"]
    assert provider.classifier_version == "openai:gpt-4.1-mini:safety_classifier.v2"


def test_embeddings_keep_input_order(provider, stub) -> None:
    stub.results["embeddings.create"] = SimpleNamespace(
        data=[SimpleNamespace(index=1, embedding=[2.0]), SimpleNamespace(index=0, embedding=[1.0])]
    )

    assert provider.embed(["a", "b"]) == [[1.0], [2.0]]
    assert stub.calls["embeddings.create"]["model"] == "text-embedding-3-small"


def test_voice_calls_use_configured_models(provider, stub) -> None:
    stub.results["transcriptions.create"] = SimpleNamespace(text=" Hola ")
    stub.results["speech.create"] = SimpleNamespace(read=lambda: b"mp3")

    text = provider.transcribe(audio=b"bytes", filename="a.webm", language="es-CL")
    audio = provider.synthesize("x" * 5000)

    assert text == "Hola" and audio == b"mp3"
    assert stub.calls["transcriptions.create"]["model"] == "gpt-4o-mini-transcribe"
    assert stub.calls["transcriptions.create"]["language"] == "es"
    assert stub.calls["speech.create"]["model"] == "gpt-4o-mini-tts"
    assert len(stub.calls["speech.create"]["input"]) == 4096
