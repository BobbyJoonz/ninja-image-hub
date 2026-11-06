from typing import Literal

from pydantic import BaseModel, Field


class LoginBody(BaseModel):
    password: str


class AccountBody(BaseModel):
    parallel_limit: int = Field(default=2, ge=1, le=5)
    name: str = Field(min_length=1, max_length=80)
    email_label: str = Field(default="", max_length=160)
    autostart: bool = False
    project_url: str = Field(default="", max_length=500)


class AccountUpdate(BaseModel):
    parallel_limit: int = Field(default=2, ge=1, le=5)
    name: str = Field(min_length=1, max_length=80)
    email_label: str = Field(default="", max_length=160)
    autostart: bool = False
    project_url: str = Field(default="", max_length=500)


class ConfirmRemove(BaseModel):
    confirmation: str


class AutoLoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=512)
    totp_secret: str = Field(default="", max_length=64)


class GenerateBody(BaseModel):
    account_id: str
    prompt: str = Field(min_length=3, max_length=4000)
    aspect_ratio: str = "1:1"
    quality_preference: str = "best"
    n: int = Field(default=1, ge=1, le=10)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)


class LoginClick(BaseModel):
    # The worker viewport is not fixed (Playwright default vs Xvfb screen), so allow the
    # full range and let the client scale by the screenshot's real pixel size.
    x: int = Field(ge=0, le=4096)
    y: int = Field(ge=0, le=4096)


class LoginType(BaseModel):
    text: str = Field(min_length=1, max_length=1024)


class LoginKey(BaseModel):
    key: str


class ControlUpdate(BaseModel):
    global_max_inflight: int | None = Field(default=None, ge=1, le=32)
    gateway_max_tabs: int | None = Field(default=None, ge=1, le=5)
    max_pending_jobs: int | None = Field(default=None, ge=1, le=10000)
    max_batch_images: int | None = Field(default=None, ge=1, le=10)
    provider_cooldown_minutes: int | None = Field(default=None, ge=1, le=240)
    max_running_accounts: int | None = Field(default=None, ge=1, le=16)
    min_free_memory_mb: int | None = Field(default=None, ge=0, le=65536)
    target_images: int | None = Field(default=None, ge=1, le=100000)
    target_window_hours: int | None = Field(default=None, ge=1, le=24)
    idle_worker_shutdown_minutes: int | None = Field(default=None, ge=0, le=1440)
    preview_retention_days: int | None = Field(default=None, ge=0, le=3650)
    export_retention_hours: int | None = Field(default=None, ge=0, le=8760)
    worker_log_max_mb: int | None = Field(default=None, ge=0, le=1024)
    min_free_disk_mb: int | None = Field(default=None, ge=0, le=1048576)
    dispatch_paused: bool | None = None


class ResolveBody(BaseModel):
    """Operator verdict for a job whose upstream outcome was unknown.

    This never retries anything: it only records what the operator verified, so a
    delivered-but-unreported image is not generated twice and a lost job can be closed.
    """

    resolution: Literal["delivered_manually", "not_delivered", "abandoned"]
    note: str = Field(default="", max_length=500)
