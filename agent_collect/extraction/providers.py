"""Swappable structured-output extraction providers."""

import json
from dataclasses import dataclass
from typing import Any, Protocol

from agent_collect.extraction.preprocess import PreparedContent
from agent_collect.extraction.prompts import SYSTEM_INSTRUCTIONS
from agent_collect.extraction.schema import openai_json_schema


@dataclass(frozen=True, slots=True)
class ExtractionRequest:
    source_url: str
    content: PreparedContent
    max_output_tokens: int
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class ProviderExtraction:
    payload: dict[str, Any]
    input_tokens: int | None = None
    output_tokens: int | None = None


class ExtractionProvider(Protocol):
    name: str
    model: str
    credential_present: bool

    def extract(self, request: ExtractionRequest) -> ProviderExtraction: ...


class ExtractionProviderError(Exception):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class FakeExtractionProvider:
    name = "fake"
    model = "fixture"
    credential_present = True

    def __init__(self, payload: dict[str, Any] | Exception) -> None:
        self.payload = payload
        self.calls: list[ExtractionRequest] = []

    def extract(self, request: ExtractionRequest) -> ProviderExtraction:
        self.calls.append(request)
        if isinstance(self.payload, Exception):
            raise self.payload
        return ProviderExtraction(self.payload)


class OpenAIExtractionProvider:
    name = "openai"

    def __init__(self, api_key: str | None, *, model: str, client: Any | None = None) -> None:
        self._api_key = api_key or ""
        self.model = model
        self._client = client

    @property
    def credential_present(self) -> bool:
        return bool(self._api_key.strip())

    def extract(self, request: ExtractionRequest) -> ProviderExtraction:
        if not self.credential_present:
            raise ExtractionProviderError("MISSING_CREDENTIAL")
        try:
            if self._client is None:
                from openai import OpenAI
                self._client = OpenAI(api_key=self._api_key, max_retries=0,
                                      timeout=request.timeout_seconds)
            response = self._client.responses.create(
                model=self.model,
                instructions=SYSTEM_INSTRUCTIONS,
                input=json.dumps({
                    "page_url": request.source_url,
                    "numbered_page_evidence": request.content.model_input,
                }, ensure_ascii=False),
                text={"format": {"type": "json_schema", "name": "conference_extraction_v2",
                                 "strict": True, "schema": openai_json_schema()}},
                max_output_tokens=request.max_output_tokens,
                store=False,
            )
        except ImportError:
            raise ExtractionProviderError("SDK_UNAVAILABLE") from None
        except Exception as error:
            name = type(error).__name__
            if name in {"APITimeoutError", "TimeoutError"}:
                raise ExtractionProviderError("TIMEOUT", retryable=True) from None
            if name == "RateLimitError":
                raise ExtractionProviderError("RATE_LIMIT", retryable=True) from None
            if name == "AuthenticationError":
                raise ExtractionProviderError("AUTHENTICATION_FAILURE") from None
            raise ExtractionProviderError("PROVIDER_ERROR", retryable=name in {"APIConnectionError", "InternalServerError"}) from None
        if getattr(response, "status", None) != "completed" or not getattr(response, "output_text", None):
            raise ExtractionProviderError("INCOMPLETE_RESPONSE")
        try:
            payload = json.loads(response.output_text)
        except (ValueError, TypeError):
            raise ExtractionProviderError("MALFORMED_RESPONSE") from None
        if not isinstance(payload, dict):
            raise ExtractionProviderError("MALFORMED_RESPONSE")
        usage = getattr(response, "usage", None)
        return ProviderExtraction(
            payload, getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None),
        )
