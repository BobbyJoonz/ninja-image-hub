"""One panel client for the operator scripts (login, CSRF, JSON requests).

Every script used to carry its own copy of the login/CSRF dance; a panel auth change had
to be applied three times. This keeps that in one place without hiding the request methods.
"""

import json
import os
import sys
from pathlib import Path

try:
    import httpx
except ImportError:  # pragma: no cover
    sys.exit("httpx is required (pip install -r requirements.txt)")


class PanelError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(f"panel HTTP {status}: {message}")
        self.status = status


def password_from(args=None, env="NINJA_ADMIN_PASSWORD"):
    value = getattr(args, "password", None) if args is not None else None
    value = value or os.getenv(env, "")
    if not value:
        sys.exit("Provide --password or NINJA_ADMIN_PASSWORD to read the panel.")
    return value


class Panel:
    """Authenticated panel client. Call login() before admin/CSRF routes."""

    def __init__(self, base_url, password, timeout=30):
        self.base_url = str(base_url).rstrip("/")
        self.password = password
        self.client = httpx.Client(base_url=self.base_url, timeout=timeout)
        self.headers = {}

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def login(self):
        response = self.client.post(
            "/api/auth/login", json={"password": self.password}
        )
        if response.status_code != 200:
            raise PanelError(response.status_code, response.text[:160])
        self.headers = {"x-csrf-token": response.json()["csrf"]}
        return self

    def request(self, method, path, **kwargs):
        options = dict(kwargs)
        merged = dict(self.headers)
        merged.update(options.pop("headers", {}) or {})
        response = self.client.request(method, path, headers=merged, **options)
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail") or response.text
            except ValueError:
                detail = response.text
            raise PanelError(response.status_code, str(detail)[:200])
        return response

    def get_json(self, path, **kwargs):
        return self.request("GET", path, **kwargs).json()

    def post_json(self, path, payload=None, **kwargs):
        return self.request("POST", path, json=payload, **kwargs).json()

    def patch_json(self, path, payload=None, **kwargs):
        return self.request("PATCH", path, json=payload, **kwargs).json()

    def delete_json(self, path, **kwargs):
        return self.request("DELETE", path, **kwargs).json()

    # Common reads used by the operator tools.
    def health(self):
        return self.client.get("/api/health").json()

    def jobs(self, limit=200):
        return self.get_json(f"/api/jobs?limit={int(limit)}")

    def queue(self):
        return self.get_json("/api/queue")

    def settings(self):
        return self.get_json("/api/settings")

    def resolve(self, job_id, resolution, note=""):
        payload = {"resolution": resolution}
        if note:
            payload["note"] = note[:500]
        return self.post_json(f"/api/jobs/{job_id}/resolve", payload)


def write_json(payload):
    print(json.dumps(payload, indent=2, ensure_ascii=False))
