import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from helpers import build_hub, client  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_gateway_ports(monkeypatch):
    """Keep test workers away from a live panel's 41000-41999 worker ports.

    Without this, a live gateway that starts while tests run can steal the port a test
    just allocated (or vice versa), producing flaky bind/health failures.
    """
    monkeypatch.setenv("GATEWAY_PORT_BASE", os.getenv("TEST_GATEWAY_PORT_BASE", "43000"))


@pytest.fixture()
def loaded(tmp_path, monkeypatch):
    module, ctx = build_hub(monkeypatch, tmp_path, runtime_installed=True)
    with client(module) as http:
        yield http, ctx
    ctx.engine.dispose()
    sys.modules.pop("app", None)


@pytest.fixture()
def loaded_offline(tmp_path, monkeypatch):
    module, ctx = build_hub(monkeypatch, tmp_path, runtime_installed=False)
    with client(module) as http:
        yield http, ctx
    ctx.engine.dispose()
    sys.modules.pop("app", None)
