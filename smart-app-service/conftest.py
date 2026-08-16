"""Pytest environment isolation for the smart-app-service unit suite.

The dev ``.env`` may enable the test plane (``TEST_DISCOVERY_SERVICE_URL`` set)
so a developer can exercise the build → promote → prod loop locally against the
same MCP. The UNIT suite must NOT inherit that: it asserts the default
(test-plane-off) behaviour and must stay hermetic + fast (no real discovery
round-trips). Pin the test plane OFF here — this runs before any test imports
``main`` / ``get_settings`` (which is ``lru_cache``d), and an ``os.environ``
value overrides the ``.env`` file in pydantic-settings.

A test that needs the test→prod flow constructs its own ``Settings`` explicitly
rather than relying on the ambient environment.
"""
import os

# Test plane off → ``Settings.test_environment_available`` is False (the suite
# baseline), regardless of the developer's local .env.
os.environ["TEST_DISCOVERY_SERVICE_URL"] = ""
# Deterministic signing for the minted test tokens (matches _test_helpers).
os.environ.setdefault("JWT_SECRET", "smart-app-service-test-secret")
os.environ.setdefault("JWT_ISSUER", "Citra-AI")
