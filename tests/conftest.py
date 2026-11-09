import os

import pytest


@pytest.fixture(autouse=True)
def _isolated_gateway_ports(monkeypatch):
    """Keep test workers away from a live panel's 41000-41999 worker ports.

    Without this, a live gateway that starts while tests run can steal the port a test
    just allocated (or vice versa), producing flaky bind/health failures.
    """
    monkeypatch.setenv("GATEWAY_PORT_BASE", os.getenv("TEST_GATEWAY_PORT_BASE", "43000"))
