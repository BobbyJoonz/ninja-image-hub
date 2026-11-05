import hmac
import secrets
import time
from fastapi import Header, HTTPException, Request
from .schemas import LoginBody


def secret_bytes(value) -> bytes:
    """UTF-8 bytes for constant-time comparison.

    ``hmac.compare_digest`` raises TypeError for non-ASCII *str* arguments, and a lone
    surrogate from a crafted JSON escape raises UnicodeEncodeError. Both turned an
    invalid credential into HTTP 500 (and skipped the failed-attempt accounting), so all
    secret comparisons go through this helper and compare bytes.
    """
    return str(value).encode("utf-8", "surrogatepass")


def require_admin(ctx, request: Request):
    if request.session.get("admin") is not True:
        raise HTTPException(status_code=401, detail="Please sign in.")


def csrf_admin(ctx, request: Request):
    ctx.require_admin(request)
    val = request.headers.get("x-csrf-token", "")
    if not val or not hmac.compare_digest(
        secret_bytes(request.session.get("csrf", "")), secret_bytes(val)
    ):
        raise HTTPException(status_code=403, detail="Invalid CSRF token.")


def require_agent(ctx, authorization: str | None = Header(default=None)):
    if (
        not ctx.AGENT_API_KEY
        or not authorization
        or (not authorization.startswith("Bearer "))
    ):
        raise HTTPException(status_code=401, detail="Invalid agent token.")
    if not hmac.compare_digest(
        secret_bytes(authorization[7:]), secret_bytes(ctx.AGENT_API_KEY)
    ):
        raise HTTPException(status_code=401, detail="Invalid agent token.")


def login(ctx, body: LoginBody, request: Request):
    ip = request.client.host if request.client else "unknown"
    recent = [t for t in ctx.login_failures.get(ip, []) if time.time() - t < 600]
    if len(recent) >= 6:
        raise HTTPException(429, "Too many attempts; retry later.")
    if not hmac.compare_digest(
        secret_bytes(body.password), secret_bytes(ctx.ADMIN_PASSWORD)
    ):
        ctx.login_failures[ip] = recent + [time.time()]
        raise HTTPException(401, "Incorrect password.")
    ctx.login_failures.pop(ip, None)
    request.session.clear()
    request.session.update({"admin": True, "csrf": secrets.token_urlsafe(32)})
    return {"ok": True, "csrf": request.session["csrf"]}


def me(ctx, request: Request):
    return {
        "authenticated": request.session.get("admin") is True,
        "csrf": (
            request.session.get("csrf", "") if request.session.get("admin") else None
        ),
    }


def logout(ctx, request: Request):
    request.session.clear()
    return {"ok": True}
