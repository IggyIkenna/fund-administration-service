"""Thin event-emission wrapper for fund-administration-service.

Guarantees every lifecycle event is tagged with ``service=fund-administration-service``
and routes through UTL's ``log_event``. Event names are passed in as strings that
must be drawn from UTL's ``event_types`` module — the caller never writes raw
``"SUBSCRIPTION_REQUESTED"`` string literals.
"""

from __future__ import annotations

from unified_trading_library import log_event

_SERVICE = "fund-administration-service"


def emit_fund_admin_event(
    event_name: str,
    details: dict[str, object],
    severity: str = "INFO",
    correlation_id: str | None = None,
) -> None:
    """Emit a fund-administration lifecycle event.

    Args:
        event_name: Event constant from ``unified_trading_library.events.event_types``
            (e.g. ``SUBSCRIPTION_REQUESTED``). Never a raw string literal at the
            call site.
        details: Event-specific payload dict. The caller's responsibility to
            serialise domain objects to primitives before passing.
        severity: One of ``INFO``, ``WARN``, ``ERROR``. Defaults to ``INFO``.
        correlation_id: Optional request/saga correlation id.
    """

    enriched: dict[str, object] = {"service": _SERVICE, **details}
    log_event(
        event_name,
        severity=severity,
        details=enriched,
        correlation_id=correlation_id,
    )
