"""Infrastructure-owned ingress facts and Guardian decisions.

Payloads must never be allowed to manufacture either their source or proof that
the Guardian evaluated them.  These objects are kept out of request schemas and
are passed only between local infrastructure components.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

from .guardiao import guardiao


@dataclass(frozen=True)
class TrustedContext:
    trusted_source: str
    trusted_user_id: str | None
    trusted_session_id: str
    request_id: str
    authenticated: bool
    authorization_scope: tuple[str, ...]
    policy_mode: str


@dataclass(frozen=True)
class GuardianEvidence:
    context: TrustedContext
    evaluation_id: str
    text_digest: str
    decision: str
    category: str = ""


def new_context(
    source: str,
    *,
    external: bool,
    trusted_user_id: str | None = None,
    authenticated: bool = False,
    authorization_scope: tuple[str, ...] = (),
    policy_mode: str | None = None,
) -> TrustedContext:
    """Create provenance in infrastructure, never from a conversation payload."""
    return TrustedContext(
        trusted_source=source,
        trusted_user_id=trusted_user_id,
        trusted_session_id=secrets.token_urlsafe(18),
        request_id=secrets.token_urlsafe(18),
        authenticated=authenticated,
        authorization_scope=authorization_scope,
        policy_mode=policy_mode or ("enforce" if external else "observation"),
    )


def evaluate_text(text: str, author: str, context: TrustedContext) -> GuardianEvidence:
    """Evaluate text once and bind the result to the exact payload bytes."""
    verdict = guardiao.avaliar(text or "", fonte=context.trusted_source, autor=author or "")
    category = ":".join((verdict.motivo or "none").split(":")[:2])
    decision = "accepted" if verdict.passa else (
        "blocked" if context.policy_mode == "enforce" else "would_block"
    )
    return GuardianEvidence(
        context=context,
        evaluation_id=secrets.token_urlsafe(18),
        text_digest=hashlib.sha256((text or "").encode("utf-8")).hexdigest(),
        decision=decision,
        category=category,
    )


async def evaluate_text_async(text: str, author: str, context: TrustedContext) -> GuardianEvidence:
    """Só a camada dura local. O revisor GPT foi removido em 24/10/2026 (ordem do Criador: barrava falas inofensivas)."""
    verdict = guardiao.avaliar(text or "", fonte=context.trusted_source, autor=author or "")
    category = ":".join((verdict.motivo or "none").split(":")[:2])
    decision = "accepted" if verdict.passa else (
        "blocked" if context.policy_mode == "enforce" else "would_block"
    )
    return GuardianEvidence(
        context=context,
        evaluation_id=secrets.token_urlsafe(18),
        text_digest=hashlib.sha256((text or "").encode("utf-8")).hexdigest(),
        decision=decision,
        category=category,
    )


def evidence_matches(evidence: object, text: str, source: str) -> bool:
    """Reject payload-shaped lookalikes and evidence for another text/source."""
    return (
        isinstance(evidence, GuardianEvidence)
        and evidence.context.trusted_source == source
        and evidence.text_digest == hashlib.sha256((text or "").encode("utf-8")).hexdigest()
    )
