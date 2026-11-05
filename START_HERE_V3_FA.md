# راهنمای سریع نسخه 0.3 Ninja Image Hub

1. قبل از هر کاری از پوشه `data/` و فایل `.env` بک‌آپ بگیر. اگر نسخه قبلی را داری، پوشه `data/` را پاک نکن و دوباره bootstrap نزن.
2. پروژه ZIP را استخراج و بسته‌ها را مطابق `README.md` نصب کن. Gateway upstream جداگانه نصب می‌شود و در ZIP وجود ندارد.
3. برای نصب تازه: `python scripts/bootstrap.py` و `python scripts/prepare_gateway.py`، سپس `python -m uvicorn app:app --host 127.0.0.1 --port 8080`.
4. در فایل `.env` در صورت تمایل تنظیم کن: `MAX_RUNNING_ACCOUNTS=2`, `GLOBAL_MAX_INFLIGHT=4`, `GATEWAY_MAX_TABS=3`, `MAX_PENDING_JOBS=1000`, `MAX_BATCH_IMAGES=4`.
5. پنل را باز کن: `http://127.0.0.1:8080`؛ Accounts → Add → Start → Login (ورود دستی) → Save Session.
6. در **Accounts → Edit** سقف موازی یک حساب `1..5` را تعیین کن. مقدار مؤثر به سقف Gateway محدود است. در **Image Studio** تعداد تصویر `n` را تعیین کن.
7. در **Jobs** خروجی‌ها و خطاها و در **Gallery** تمام تصاویر یک سفارش را ببین. در صورت queued بودن، Cancel امکان‌پذیر است؛ برای running این امکان وجود ندارد.
8. اتصال ایجنت: `POST /v1/images/generations` با توکن Agent و مقادیر `account_id`, `prompt`, `aspect_ratio`, `n`, `idempotency_key`؛ سپس GET `/v1/jobs/{id}` و URLهای آرایه `images`.
9. API صف `/api/queue` تعداد pending/running و متریک‌های مشاهده‌شده را می‌دهد. سرعت واقعی هر اکانت Plus نامعلوم و بدون تضمین است.

**قبل از آپگرید:** بک‌آپ محلی لازم است. مهاجرت‌های موجود additive هستند، اما راه بازگشت خودکار و پشتیبانی رسمی دیتابیس توزیع‌شده در MVP وجود ندارد.

**تست‌ها:** `python -m pytest -q` و `node --check frontend/app.js`.

**محدودیت‌ها:** دسترسی برنامه‌نویسی‌شده به ChatGPT UI خلاف شرایط استفاده OpenAI است؛ محدودیت‌های حساب را دور نزن و برای تولید تجاری از مسیر مجاز استفاده کن.

جزئیات کلیدی را در `docs/CONCURRENCY_ARCHITECTURE_FA.md` و `AGENTS.md` بخوان.
