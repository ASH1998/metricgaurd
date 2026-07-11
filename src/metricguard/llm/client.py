"""LLM judgment layer — LangChain, provider-agnostic.

Model is selected via LLM_MODEL (provider-prefixed, e.g.
"anthropic:claude-opus-4-8", "openai:gpt-4o") so any provider works by
swapping one env var and installing the matching langchain-* package.

Design rule (context.md): the LLM consumes deterministic outputs — signatures,
conflict reports, divergence numbers — and produces judgment: plain-language
explanations, canonical proposals, clustering calls. It never computes the
verifiable math itself.
"""

from __future__ import annotations

from functools import lru_cache

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

from metricguard.config import settings
from metricguard.llm.prompts import EXPLAIN_CONFLICT_PROMPT, SYSTEM_PROMPT
from metricguard.models import (
    ConflictExplanation,
    ConflictReport,
    DivergenceReport,
    MetricDefinition,
)


@lru_cache(maxsize=1)
def get_llm(model: str | None = None) -> BaseChatModel:
    """Provider-agnostic chat model. `model` overrides LLM_MODEL for tests."""
    return init_chat_model(model or settings.llm_model)


def explain_conflict(
    definitions: list[MetricDefinition],
    conflict: ConflictReport,
    divergence: DivergenceReport | None = None,
) -> ConflictExplanation:
    """Plain-language explanation + ranked canonical proposals with tradeoffs.

    Structured output is schema-enforced via LangChain, so callers get a
    validated ConflictExplanation, not free text to parse.
    """
    llm = get_llm().with_structured_output(ConflictExplanation)

    context_parts = [
        "## Candidate definitions",
        *(
            f"### {d.name} (source: {d.source or 'unknown'}, owner: {d.owner or 'unknown'})\n"
            f"```sql\n{d.sql.strip()}\n```\n"
            f"Signature: {d.signature.model_dump_json() if d.signature else 'not extracted'}"
            for d in definitions
        ),
        "## Deterministic conflict report (computed by code, trust these facts)",
        conflict.model_dump_json(indent=2),
    ]
    if divergence is not None:
        context_parts += [
            "## Executed divergence proof (computed by code, trust these numbers)",
            divergence.model_dump_json(indent=2),
        ]

    return llm.invoke([
        ("system", SYSTEM_PROMPT),
        ("human", EXPLAIN_CONFLICT_PROMPT.format(context="\n\n".join(context_parts))),
    ])
