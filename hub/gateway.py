"""Gateway response transport with a hard JSON envelope budget and no replay."""

import json
import tempfile
from types import SimpleNamespace

import httpx

MAX_JSON_BYTES = 32 * 1024 * 1024


class ResponseBudgetError(Exception):
    """Submission may have succeeded; the outcome must remain unknown."""


def request_generation(url, secret, payload, n):
    with httpx.Client(
        trust_env=False,
        timeout=httpx.Timeout(read=720.0 * n, connect=10, write=30, pool=10),
    ) as client:
        with client.stream(
            "POST", url, headers={"Authorization": "Bearer " + secret}, json=payload
        ) as response:
            status = response.status_code
            headers = dict(response.headers)
            # Spool JSON to disk, avoiding raw bytes + decoded text + parsed data
            # living concurrently in RAM. URL responses are normally tiny.
            with tempfile.TemporaryFile() as spool:
                total = 0
                for chunk in response.iter_bytes(256 * 1024):
                    total += len(chunk)
                    if total > MAX_JSON_BYTES:
                        raise ResponseBudgetError(
                            "Gateway response exceeded 32 MiB; inspect upstream, never auto-retry."
                        )
                    spool.write(chunk)
                spool.seek(0)
                try:
                    data = json.load(spool)
                except (ValueError, UnicodeError):
                    data = None
    return SimpleNamespace(status_code=status, headers=headers, data=data)
