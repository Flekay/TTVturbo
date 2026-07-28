"""Research and LLM adapters for Ideas Research.

The service never performs network calls or imports a concrete LLM
framework directly.  Instead it depends on two protocols:

* :class:`ResearchProvider` — searches current sources for a set of
  topics and returns normalised :class:`RawSource` records.
* :class:`LLMAdapter` — runs an Instruct or Thinking profile prompt and
  returns parsed JSON.

Default adapters:

* :class:`UnavailableResearchProvider` / :class:`UnavailableLLMAdapter`
  raise :class:`IdeasResearchUnavailableError` so the service surfaces a
  clean error (and tests can inject fakes).
* :class:`StaticResearchProvider` / :class:`StaticLLMAdapter` return
  canned results for tests.  **No real network calls happen in standard
  tests.**
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .schemas import (
    IdeasResearchUnavailableError,
    LLMProfile,
    Reliability,
)

logger = logging.getLogger("ttvturbo.ideas_research.providers")


# ---------------------------------------------------------------------------
# Raw source (provider output, before normalisation/dedup)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RawSource:
    """A raw source returned by a research provider.

    The provider returns as much as it can; the service normalises,
    deduplicates and assigns reliability bands.  ``published_at`` is an
    ISO-8601 string (may be empty).  ``growth_signal`` is an optional
    non-negative signal (e.g. view delta) in 0..1.
    """

    url: str
    title: str = ""
    publisher: str = ""
    published_at: str = ""
    summary: str = ""
    growth_signal: float = 0.0
    # Provider hint for the reliability band; the service still validates.
    reliability_hint: str = Reliability.UNKNOWN.value


# ---------------------------------------------------------------------------
# Research provider protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class ResearchProvider(Protocol):
    """Protocol every research provider must satisfy.

    ``search`` receives the research request fields and returns a list
    of :class:`RawSource`.  The service validates and normalises the
    output strictly.
    """

    def search(
        self,
        topics: list[str],
        *,
        language: str,
        time_range: str,
        max_topics: int,
    ) -> list[RawSource]:
        ...

    def available(self) -> bool:
        """True if the provider can produce results right now."""
        ...


class UnavailableResearchProvider:
    """Default provider when no research backend is configured."""

    def search(
        self,
        topics: list[str],
        *,
        language: str,
        time_range: str,
        max_topics: int,
    ) -> list[RawSource]:
        raise IdeasResearchUnavailableError(
            "no research provider configured for ideas research"
        )

    def available(self) -> bool:
        return False


class StaticResearchProvider:
    """A test / fixture provider that returns canned :class:`RawSource`.

    ``results`` is a list returned verbatim (in order).  ``fail_next``
    makes the next ``search`` call raise
    :class:`IdeasResearchUnavailableError` once, then clears the flag —
    useful for testing the retry path.
    """

    def __init__(
        self,
        results: list[RawSource] | None = None,
        *,
        fail_next: bool = False,
    ) -> None:
        self._results = list(results or [])
        self.fail_next = fail_next

    def search(
        self,
        topics: list[str],
        *,
        language: str,
        time_range: str,
        max_topics: int,
    ) -> list[RawSource]:
        if self.fail_next:
            self.fail_next = False
            raise IdeasResearchUnavailableError("static research provider: fail_next")
        return list(self._results)

    def available(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# LLM adapter protocol
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LLMResponse:
    """The parsed response of an LLM call.

    ``content`` is the raw text returned by the model.  ``json`` is the
    parsed JSON object when the prompt requested JSON and parsing
    succeeded, otherwise ``None``.
    """

    content: str
    json: dict[str, Any] | None = None
    profile: str = LLMProfile.INSTRUCT.value


@runtime_checkable
class LLMAdapter(Protocol):
    """Protocol every LLM adapter must satisfy."""

    def run(
        self,
        prompt: str,
        *,
        profile: str = LLMProfile.INSTRUCT.value,
        response_json: bool = False,
    ) -> LLMResponse:
        ...

    def available(self) -> bool:
        """True if the adapter can produce results right now."""
        ...


class UnavailableLLMAdapter:
    """Default adapter when no LLM is configured."""

    def run(
        self,
        prompt: str,
        *,
        profile: str = LLMProfile.INSTRUCT.value,
        response_json: bool = False,
    ) -> LLMResponse:
        raise IdeasResearchUnavailableError(
            "no LLM configured for ideas research"
        )

    def available(self) -> bool:
        return False


@dataclass
class StaticLLMAdapter:
    """A test / fixture LLM adapter.

    ``responses`` maps a prompt *key* (substring) to a canned
    :class:`LLMResponse`.  When no key matches, ``default`` is returned.
    ``fail_next`` makes the next call raise
    :class:`IdeasResearchUnavailableError` once.
    """

    responses: dict[str, LLMResponse] = field(default_factory=dict)
    default: LLMResponse | None = None
    fail_next: bool = False
    # Records every call for assertions.
    calls: list[tuple[str, str, bool]] = field(default_factory=list)

    def run(
        self,
        prompt: str,
        *,
        profile: str = LLMProfile.INSTRUCT.value,
        response_json: bool = False,
    ) -> LLMResponse:
        self.calls.append((prompt, profile, response_json))
        if self.fail_next:
            self.fail_next = False
            raise IdeasResearchUnavailableError("static llm adapter: fail_next")
        for key, resp in self.responses.items():
            if key in prompt:
                return resp
        if self.default is not None:
            return self.default
        # Fallback: echo a minimal valid JSON for json requests.
        if response_json:
            return LLMResponse(content="{}", json={}, profile=profile)
        return LLMResponse(content="", profile=profile)

    def available(self) -> bool:
        return True
