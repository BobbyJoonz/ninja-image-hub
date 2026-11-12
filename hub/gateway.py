"""Gateway response transport with a hard JSON envelope budget and no replay."""

import json
import tempfile
from collections.abc import Mapping, Sequence
from types import SimpleNamespace

import httpx

MAX_JSON_BYTES = 32 * 1024 * 1024

FileTuple = tuple[str, tuple[str, object, str]]


class ResponseBudgetError(Exception):
    """Submission may have succeeded; the outcome must remain unknown."""


def request_generation(
    url: str,
    secret: str,
    payload: Mapping[str, object],
    n: int,
    files: Sequence[FileTuple] | None = None,
) -> SimpleNamespace:
    """POST a generation request.

    ``files`` is an optional list of httpx multipart file tuples for reference images:
    ``[("reference_images", (filename, fileobj, mime)), ...]``. When present the gateway
    receives multipart/form-data (its /v1/images/generations accepts this and the UI
    uploads each file into the composer); otherwise the request stays plain JSON.
    """
    request_kwargs: dict[str, object] = {"json": payload}
    if files:
        request_kwargs = {
            "data": {key: str(value) for key, value in payload.items()},
            "files": files,
        }
    with httpx.Client(
        trust_env=False,
        timeout=httpx.Timeout(read=720.0 * n, connect=10, write=120, pool=10),
    ) as client:
        with client.stream(
            "POST", url, headers={"Authorization": "Bearer " + secret}, **request_kwargs
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


def request_chat(
    url: str,
    secret: str,
    payload: Mapping[str, object],
    timeout: float = 180.0,
) -> SimpleNamespace:
    """POST a text/review turn to the gateway (same conversation when conversation_id is set)."""
    with httpx.Client(trust_env=False, timeout=httpx.Timeout(read=timeout, connect=10)) as client:
        response = client.post(
            url, headers={"Authorization": "Bearer " + secret}, json=payload
        )
        try:
            data = response.json()
        except ValueError:
            data = None
    return SimpleNamespace(
        status_code=response.status_code, headers=dict(response.headers), data=data
    )
