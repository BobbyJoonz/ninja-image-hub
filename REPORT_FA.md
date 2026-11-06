# گزارش نسخهٔ ۰٫۶٫۰ — سخت‌سازی Ninja Image Hub

تاریخ: ۱۵ آبان ۱۴۰۵ / ۶ نوامبر ۲۰۲۶

v0.7.0 یک انتشار **سخت‌سازی + بهره‌برداری** است، نه افزودن قابلیت تولید تصویر. کد، تست‌ها و اسکریپت‌ها نهایی‌اند؛ این سند فقط تغییرات و شواهد را ثبت می‌کند. این پروژه همچنان ابزار محلی و آزمایشی است و هیچ SLA برای حساب Plus ارائه نمی‌دهد.

## چه چیزی در v0.5 شکسته بود

- روی ویندوز، رجیستری پسوند `.js` را به `text/plain` نگاشت می‌کرد؛ مرورگر ماژول ES را اجرا نمی‌کرد و پنل روی صفحهٔ ورود می‌ماند. کلیک «Sign in» **هیچ درخواستی به `/api/*`** نمی‌فرستاد.
- مقایسهٔ رمز با `hmac.compare_digest` روی `str` انجام می‌شد؛ گذرواژهٔ غیر ASCII (مثلاً فارسی) در `login` خطای `TypeError` می‌داد و HTTP 500 برمی‌گرداند.
- فرایند Node دروازهٔ مرورگر کل `os.environ` را به ارث می‌برد؛ یعنی `APP_SECRET`، `ADMIN_PASSWORD`، `AGENT_API_KEY` و `DATABASE_URL` در اختیار کد شخص ثالث و فرزندان مرورگرش بود.
- SQLite بدون WAL و با timeout پیش‌فرض اجرا می‌شد؛ نوشتن هم‌زمان scheduler و رشته‌های Job می‌توانست «database is locked» بدهد و تصویری را که تازه منتشر شده بود از دست بدهد.
- probe سلامت workerها داخل قفل جهانی صف انجام می‌شد؛ هر حساب تا حدود ۰٫۶ ثانیه صف را متوقف می‌کرد و `POST /api/generate` و پاک‌سازی Job را کند می‌کرد.
- اگر probe پیش از ارسال شکست می‌خورد، Job به‌جای بازگشت به صف، **به‌طور قطعی failed** می‌شد؛ درحالی‌که هیچ درخواستی به ارائه‌دهنده نرسیده بود.
- worker خراب‌شده خودکار راه‌اندازی نمی‌شد و توقف دستی اپراتور هم از راه‌اندازی مجدد قابل تشخیص نبود.
- `prepare_gateway.py` پس از `npm ci` متوقف می‌شد و Playwright/patchright را نصب نمی‌کرد؛ worker هرگز نمی‌توانست مرورگر باز کند.
- کلید idempotency درخواست در حافظهٔ تب بود؛ reload در میانهٔ ارسال مبهم، کلید را از دست می‌داد و Job تکراری می‌ساخت.
- ساخت `preview/dist` با encoding لوکال و بازنویسی CRLF روی ویندوز، byte-reproducible نبود.
- تست symlink در `tests/test_delivery.py` روی میزبان بدون دسترسی symlink به‌جای skip، خطا می‌داد.
- هیچ endpoint سلامت عمومی، doctor پیش از اجرا و سرور preview با MIME درست وجود نداشت.

## چه چیزی در v0.6 اصلاح شد

| مشکل (v0.5) | رفع (v0.6) | شاهد |
|---|---|---|
| MIME اشتباه `.js` روی ویندوز، بوت‌نشدن پنل | در `hub/factory.py` هنگام import، `text/javascript` برای `.js` و `.mjs` و `application/json` برای `.json` pin می‌شود | تست MIME؛ Chrome واقعی: ۷ ماژول JS بارگذاری و داشبورد رندر شد |
| HTTP 500 برای گذرواژهٔ غیر ASCII | مقایسهٔ گذرواژه و کلید ایجنت در `hub/security.py` روی byteهای UTF-8 انجام می‌شود | تست ورود با گذرواژهٔ فارسی؛ ۴۰۱ برای رمز غلط |
| نشت secrets به فرایند Node | `WORKER_ENV_ALLOWLIST` و `worker_env()` در `hub/workers.py`؛ فقط متغیرهای OS مجاز به ارث می‌رسند | تست `worker_env`؛ نبودِ چهار secret حساس در env فرزند |
| «database is locked» و از دست رفتن تصویر | `journal_mode=WAL`، `busy_timeout=15000`، `synchronous=NORMAL` و timeout اتصال ۱۵ ثانیه در `hub/database.py` | تست PRAGMA WAL؛ مسیر save دیگر با قفل SQLite شکست نمی‌خورد |
| probe سلامت داخل قفل صف | `_refresh_health` بیرون از قفل، snapshot را در `health_cache` می‌گذارد و dispatcher فقط می‌خواند | بازبینی کد؛ ترتیب حلقه supervise → refresh → dispatch |
| failed شدن Job بدون ارسال | شکست probe پیش از ارسال، Job را به `queued` برمی‌گرداند (`started_at=None`) | تست requeue؛ وضعیت نهایی `queued` |
| worker خراب برنمی‌گشت | `_supervise_workers` با backoff ۳۰/۱۲۰/۶۰۰/۱۸۰۰ ثانیه و سقف ۸ تلاش (Start دستی دوباره فعالش می‌کند)؛ توقف اپراتور در `suppressed_workers` ثبت می‌شود و احیا نمی‌شود | تست supervisor و suppression؛ E2E با kill کردن worker |
| نصب ناقص دروازه (بدون مرورگر) | `prepare_gateway.py` بازنویسی شد: `npm ci` و سپس `npx playwright install chromium` و `npx patchright install chrome`؛ `--skip-browsers` برای نصب دستی | تست متن اسکریپت؛ doctor وجود Chrome و کش Playwright را بررسی می‌کند |
| از دست رفتن کلید idempotency با reload | کلید درخواست در `sessionStorage` همان تب نگه داشته می‌شود | تست/بازبینی `frontend/app.js`؛ رفتار E2E |
| preview غیرقابل بازتولید | `preview/dist` بازسازی شد و با `frontend/` byte-identical است | تست بازتولید بایت‌به‌بایت |
| symlink test روی ویندوز بدون privilege | تست symlink در `tests/test_delivery.py` روی چنین میزبانی `pytest.skip` می‌شود | ۱ تست skip در اجرای نهایی |
| نبود ابزار preflight و preview server | `scripts/doctor.py` و `scripts/serve_preview.py` اضافه شدند؛ `GET /api/health` بدون احراز هویت | تست doctor و health؛ اجرای واقعی روی همین ماشین |
| فقدان شمارندهٔ سلامت ماشین‌خوان | `hub/system.py: health(ctx)` فقط شمارنده/بولین برمی‌گرداند: بدون path، prompt، secret یا cookie | تست «health عمومی است و path لو نمی‌دهد» |

## شواهد اجراشده

- `python -m pytest -q` → **۸۶ passed، ۱ skipped** (حدود ۶۰ ثانیه). تست skip مربوط به symlink روی میزبان بدون privilege است.
- `node --test tests/frontend.test.mjs` → **۵ pass**.
- `node --check frontend/app.js` و هر ۶ ماژول `frontend/modules/` و `preview/demo.js` → **OK**.
- E2E زنده روی کد اصلاح‌شده با دروازهٔ جعلیِ سازگار با قرارداد: worker آنلاین و `session: connected`؛ Job با `n=2` → `completed` با **۲ فایل اصلی متمایز**؛ گالری **۲ آیتم**؛ خروجی ZIP با کد **۲۰۰** و امضای **`PK`**؛ kill کردن فرایند worker به‌طور خودکار بازیابی شد و Job بعدی هم `completed` شد. این E2E با دروازهٔ شبیه‌سازی‌شده است، نه ChatGPT Plus واقعی.
- Chrome واقعی (headless) روی اپلیکیشن واقعی: **۷ ماژول JS** بارگذاری شد، ورود موفق بود و داشبورد رندر شد. پیش از رفع، کلیک Sign in **صفر** درخواست `/api/*` می‌فرستاد و پنل روی پوستهٔ ورود می‌ماند.
- `python scripts/doctor.py` روی همین ماشین، پیش از setup: **۱۰ pass / ۳ warn / ۴ fail** (FAIL ها: نبود `.env`، `APP_SECRET`، `ADMIN_PASSWORD` و نبود دروازه؛ WARN ها: MIME پیش‌فرض ویندوز، پورت ۸۰۸۰ اشغال، `AGENT_API_KEY` خالی) — روی clone تازه انتظار می‌رود.
- `preview/dist` پس از بازسازی با `frontend/` byte-identical است.
- روی همین ماشین، `github.com:443` در دسترس نبود؛ نصب دروازه از tarball سایت `codeload.github.com` انجام شد (۱۲۴ پکیج `npm ci`) و دانلود مرورگرهای Playwright توسط میزبانی geo-blocked رد شد، اما worker با **Google Chrome نصب‌شده** (`channel=chrome`) بالا آمد، به `chatgpt.com` وصل شد و وضعیت `online` گرفت. این مسیرهای جایگزین اکنون در اسکریپت نصب تعبیه شده‌اند.

## workers روی تقاضا

هر اکانت روشن یک Chrome واقعی (~۱GB رم) نگه می‌داشت. اکنون worker بی‌کار پس از `idle_worker_shutdown_minutes` (پیش‌فرض ۲۰) متوقف می‌شود و با اولین Job یا باز شدن صفحهٔ Login خودکار بیدار می‌شود؛ سشن از `session.json` بازیابی می‌شود. حساب Stopشدهٔ اپراتور بیدار نمی‌شود. gateway یتیمِ پنلِ hard-killed فقط اگر متعلق به همان حساب باشد بسته می‌شود و کل درخت پروسه (Node+Chrome) پاک می‌شود.

**اثبات زنده:** idle=1 → پس از ۳۷ ثانیه `running_workers=0, idle_workers=1`؛ سپس Job → بیدارباش خودکار → `completed` با ۱ تصویر.

## فایل‌های تغییرکرده

| فایل | تغییر |
|---|---|
| `hub/factory.py` | pin کردن MIME در import؛ ثبت `GET /api/health`؛ bind کردن `_refresh_health` و `_supervise_workers`؛ نسخهٔ FastAPI برابر 0.6.0 |
| `hub/system.py` | تابع جدید `health(ctx)` با `ok`, `version: "0.6.0"`, `gateway_installed`, `accounts`, `running_workers`, `dispatch_paused` و شمارنده‌های وضعیت Job |
| `hub/database.py` | WAL، `busy_timeout=15000`، `synchronous=NORMAL`، timeout اتصال ۱۵ ثانیه |
| `hub/security.py` | مقایسهٔ گذرواژه و کلید ایجنت روی byteهای UTF-8 |
| `hub/workers.py` | `WORKER_ENV_ALLOWLIST` و `worker_env()`؛ ثبت توقف اپراتور در `ctx.suppressed_workers` هنگام `stop_worker` |
| `hub/scheduler.py` | probe سلامت بیرون از قفل؛ `_supervise_workers` با backoff و سقف ۸ تلاش؛ requeue در شکست probe پیش از ارسال؛ ترتیب supervise → refresh → dispatch |
| `hub/runtime.py` | افزودن `health_cache`، `suppressed_workers`، `worker_restart_state` |
| `frontend/app.js` | نگه‌داشتن کلید idempotency در `sessionStorage` در طول reload |
| `preview/dist` | بازسازی و byte-identical با `frontend/` |
| `scripts/prepare_gateway.py` | بازنویسی کامل؛ `--yes` و `--skip-browsers`؛ الزام `package-lock.json`؛ ثبت `vendor/PINNED_COMMIT.txt`؛ `npm ci` + نصب هر دو مرورگر |
| `scripts/doctor.py` | doctor جدید با `--json` و کد خروج ۰/۱ |
| `scripts/serve_preview.py` | سرور استاتیک لوکال با MIME درست، فقط `preview/dist` |
| `tests/test_hardening.py` | ۱۰ تست رگرسیون جدید |
| `tests/test_delivery.py` | skip کردن تست symlink روی میزبان بدون privilege |

## موارد تأییدنشده (نباید به‌عنوان واقعیت ارائه شوند)

- ورود و تولید واقعی با حساب ChatGPT Plus.
- رفتار Cloudflare/Turnstile.
- تولید ۵۰۰ تصویر در ۵ ساعت.
- PostgreSQL.
- چند replica / چند فرایند Uvicorn.
- `Best/Balanced/Draft` همچنان فقط راهنمای پرامپت است، نه پارامتر رسمی کیفیت.

## ارتقا از v0.5

۱. ارسال کار جدید را Pause کنید و منتظر پایان Jobهای فعال بمانید؛ سپس فرایند Uvicorn قدیمی را متوقف کنید.
۲. از `.env` و **کل `DATA_DIR`** (پیش‌فرض `data/`) بک‌آپ بگیرید. با WAL، فایل `hub.db` به‌تنهایی کافی نیست؛ کل پوشهٔ داده یا خروجی `sqlite3 .backup` لازم است.
۳. سورس جدید را جایگزین کنید، اما `.env` و داده‌ها را حفظ کنید. روی نصب موجود `bootstrap.py` را دوباره اجرا نکنید.
۴. `pip install -r requirements.txt` و سپس `python scripts/doctor.py`. مهاجرت SQL مهاجرت افزایشی است؛ WAL در نخستین اتصال اعمال می‌شود. Jobهای `running` باقی‌مانده از قطع ناگهانی، `unknown` می‌شوند.
۵. فقط **یک** فرایند اجرا کنید: `python -m uvicorn app:app --host 127.0.0.1 --port 8081 --workers 1`.
۶. یک Job کوچک آزمایشی بفرستید و وضعیت/خروجی را بررسی کنید. مسیرهای API و دادهٔ v0.1 تا v0.5 حفظ شده‌اند.

## نکتهٔ محیطی مهم

روی این ماشین **Adobe Connect پورت `127.0.0.1:8080` را اشغال می‌کند**؛ اجرای مستندِ قبلی با `--port 8080` خطای «address already in use» می‌دهد. مثال امن `--port 8081` است و `doctor.py` در صورت اشغال بودن پورت، یک پورت آزاد پیشنهاد می‌دهد.

برای گام‌به‌گام نصب [START_HERE_FA.md](START_HERE_FA.md)، برای اولین اجرای عمیق [docs/FIRST_RUN_FA.md](docs/FIRST_RUN_FA.md) و برای بهره‌برداری [docs/OPERATIONS_FA.md](docs/OPERATIONS_FA.md) را ببینید.


## فاز ۴ — بهره‌برداری (v0.7.0)

هدف: از «کار می‌کند» به «قابل اتکا و قابل اندازه‌گیری». UI عمداً دست نخورد مگر برای همین قابلیت‌ها.

| قابلیت | پیاده‌سازی | تست |
|---|---|---|
| retention + گارد دیسک | tick ساعتی scheduler؛ فقط preview/export/incoming/log؛ dispatch در دیسک کم متوقف می‌شود | ۳ تست: حذف فقط مشتقات، حفاظت لاگ worker زنده، توقف dispatch |
| آشتی `unknown` | `POST /api/jobs/{id}/resolve` یک‌بارمصرف + audit بدون note + UI دیالوگ | تست: ۴۰۳/۴۲۲/۴۰۹/۲۰۰، health counter، audit |
| بکاپ/ریستور | `scripts/backup.py` (SQLite backup API + manifest sha256 + verify/restore) | تست round-trip + تشخیص دستکاری |
| هشدار | `scripts/watch.py` (unreachable/disk/unknown/stall/queue-age + webhook) | تست evaluate برای ۵ شرط |
| اندازه‌گیری ظرفیت | `scripts/capacity_report.py` (read-only + probe دومرحله‌ای) | تست percentile/summarize |
| تنظیمات زنده | چهار تنظیم جدید در `control_settings` + Operations + `/api/settings` | تست اعتبارسنجی/ماندگاری |

**اعداد تست:** `pytest` → **۹۹ passed، ۱ skipped**؛ `node --test` → **۶ pass**؛ `doctor.py` و `build_icons.py --check` سبز؛ `preview/dist` byte-identical.

**هنوز تأییدنشده (باید با اکانت واقعی سنجیده شود):** نرخ واقعی ۱۰ اکانت و ۵۰۰ تصویر/۵ ساعت. ابزار اندازه‌گیری آماده است (`scripts/capacity_report.py`)، اما هیچ عدد واقعی provider تا اجرای آن روی سرور در این گزارش ثبت نمی‌شود.
