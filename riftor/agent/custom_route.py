"""litellm registry-collision route markers for custom providers.

litellm matches bare model names against its built-in registry and may hijack
ids (e.g. ``gpt-5.5``) to a first-party provider, bypassing a ``CustomLLM``
handler. Custom providers prefix the bare name with an opaque marker so
litellm routes to the handler; the handler strips the marker before calling the
real API.
"""

from __future__ import annotations


def route_marker(provider_key: str) -> str:
    """Return the opaque litellm route marker for ``provider_key``.

    Sanitizes the key to alphanumeric characters, prefixes ``riftor``, and
    suffixes ``-``. E.g. ``"codex"`` -> ``"riftorcodex-"``.
    """
    sanitized = "".join(c for c in provider_key if c.isalnum())
    return f"riftor{sanitized}-"


def to_litellm_model(
    model: str, *, provider_key: str, marker: str | None = None
) -> str:
    """Map a user/config model id to the id handed to litellm.

  If ``model`` starts with ``{provider_key}/``, insert ``marker`` into the bare
  name so litellm cannot registry-match it. Idempotent when the marker is already
  present. Non-matching ids pass through unchanged.
    """
    if marker is None:
        marker = route_marker(provider_key)
    prefix = f"{provider_key}/"
    if not model.startswith(prefix):
        return model
    bare = model[len(prefix) :]
    if bare.startswith(marker):
        return model
    return f"{prefix}{marker}{bare}"


def bare_model(model: str, *, provider_key: str, marker: str | None = None) -> str:
    """Strip the ``{provider_key}/`` prefix and route marker, yielding the real model id."""
    if marker is None:
        marker = route_marker(provider_key)
    prefix = f"{provider_key}/"
    bare = model[len(prefix) :] if model.startswith(prefix) else model
    if bare.startswith(marker):
        bare = bare[len(marker) :]
    return bare
