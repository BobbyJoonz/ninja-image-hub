# بهره‌برداری، ظرفیت و حالت‌های خرابی — Ninja Image Hub v0.6

این سند برای اپراتوری است که نصب را انجام داده و می‌خواهد بداند ظرفیت واقعی نرم‌افزار چقدر است، خرابی‌ها را چگونه تشخیص دهد و چه کارهایی ممنوع است. هیچ عددی در این سند SLA ارائه‌دهنده یا تضمین تولید ChatGPT Plus نیست.

## ۱. ریاضیات ظرفیت (سقف نرم‌افزار، نه سهمیهٔ ارائه‌دهنده)

### حافظه

- هر worker مرورگر Chrome در عمل حدود **۰٫۴ تا ۰٫۷ گیگابایت RAM** مصرف می‌کند (بسته به تب‌ها و صفحهٔ ChatGPT).
- تنظیم پیش‌فرض `max_running_accounts=2` است؛ یعنی تقریباً **۰٫۸ تا ۱٫۴ گیگابایت** فقط برای مرورگرها، به‌علاوهٔ سربار پنل Python.
- `max_running_accounts` تا ۱۶ قابل تنظیم است، ولی RAM میزبان را قبل از افزایش اندازه بگیرید. سقف نرم‌افزاری به معنی توان سخت‌افزار نیست.
- `global_max_inflight` پیش‌فرض ۴ و تا ۱۶ است؛ این سقف *درخواست* است، نه تعداد Chrome و نه نرخ پذیرش upstream.

### پهنای باند و زمان‌بندی

- `MIN_JOB_GAP_SEC=60` در محیط worker تنظیم می‌شود؛ یعنی هر تب بین Jobها حداقل ۶۰ ثانیه فاصله دارد. `MIN_TURN_STAGGER_SEC=10` فاصلهٔ شروع نوبت‌ها در تب‌های مختلف است.
- `gateway_max_tabs=3` سقف تب هر worker و `parallel_limit` هر حساب (پیش‌فرض ۲، مؤثر `min(parallel_limit, gateway_max_tabs)`) سقف Job هم‌زمان همان حساب است.
- مثال سقف زمان‌بندی با فرض پذیرش همهٔ درخواست‌ها و `n=1`:

| ترکیب | تب مؤثر | سقف درخواست در ساعت |
|---|---:|---:|
| ۱ حساب، ۱ تب | 1 | ۶۰ |
| ۱ حساب، ۲ Job موازی | 2 | ۱۲۰ |
| ۲ حساب، هرکدام ۲ Job | 4 | ۲۴۰ |
| ۲ حساب، هرکدام ۳ تب | 6 | ۳۶۰ |

- اگر هر درخواست `n` تصویر برگرداند، تعداد تصویر می‌تواند تا `n` برابر شود؛ اما خروجی ناقص، رد شدن، cooldown و تأخیر غیرخطی باعث می‌شود این عدد **سقف تئوریک** باشد. هدف «۵۰۰ تصویر در ۵ ساعت» با این تنظیمات از نظر سقف نرم‌افزاری ممکن به نظر می‌رسد، ولی **تأیید نشده** است و به سهمیهٔ واقعی حساب بستگی دارد.

### دیسک

- هر PNG اصلی حدود **۲ تا ۵ مگابایت** است. مثال: ۱۰۰ تصویر ≈ ۰٫۲ تا ۰٫۵ گیگابایت؛ ۵۰۰ تصویر ≈ ۱ تا ۲٫۵ گیگابایت؛ ۱۰۰۰ تصویر ≈ ۲ تا ۵ گیگابایت.
- به این مقدار، پیش‌نمایش‌های WebP، فایل‌های موقت export، لاگ workerها و رشد `hub.db` را اضافه کنید. فضای آزاد را با doctor و دوره‌ای بررسی کنید.
- تصویر بزرگ‌تر از ۶۴ MiB یا بالاتر از ۹۰ میلیون پیکسل پذیرفته نمی‌شود؛ پیش‌نمایش برای بالای ۲۴ مگاپیکسل ساخته نمی‌شود ولی اصل قابل دانلود است.

## ۲. حالت‌های خرابی

| حالت | چه اتفاقی می‌افتد | کار اپراتور |
|---|---|---|
| `unknown` | ارسال upstream انجام شده اما نتیجه (timeout، پاسخ ناخوانا، خطای 5xx) نامعلوم است | **بازتولید نکنید.** ابتدا در ChatGPT/دروازه بررسی کنید؛ Hub replay خودکار ندارد |
| `rate_limited` | پاسخ 429 یا `rate_limit`؛ همان حساب تا `max(provider_cooldown_minutes, Retry-After)` در `cooldown_until` می‌ماند | منتظر پایان cooldown بمانید؛ حساب عوض نکنید و محدودیت را دور نزنید |
| worker crash | supervisor در حلقهٔ بعدی آن را تشخیص می‌دهد و با backoff **۳۰/۱۲۰/۶۰۰/۱۸۰۰ ثانیه** دوباره راه می‌اندازد؛ پس از **۸ تلاش ناموفق** خودکار متوقف می‌شود و باید دستی Start کنید | لاگ `data/accounts/<id>/worker.log` و `doctor.py` را ببینید. اگر پشت‌سرهم crash می‌کند، نصب مرورگر/دروازه یا نشست را بررسی کنید |
| توقف دستی worker | `stop_worker` حساب را در `suppressed_workers` ثبت می‌کند | supervisor آن را زنده نمی‌کند. برای اجرای دوباره، از Accounts دکمهٔ Start را بزنید |
| شکست probe پیش از ارسال | Job به `queued` برمی‌گردد (`started_at=None`) و بعداً دوباره تلاش می‌شود | کاری لازم نیست؛ در صورت تکرار، سلامت worker و پورت را بررسی کنید |
| ری‌استارت اپلیکیشن | Jobهای `running` در شروع به `unknown` تبدیل می‌شوند؛ Jobهای `queued` می‌مانند؛ workerهای autostart دوباره شروع می‌شوند | Jobهای `unknown` را دستی بررسی کنید؛ هیچ صفی حذف نمی‌شود |
| دروازه نصب نیست | `startup_workers` worker راه نمی‌اندازد؛ `gateway_installed=false` در `/api/health` و `/api/settings` | `python scripts/prepare_gateway.py` و سپس doctor |
| پورت worker اشغال است | شروع worker رد می‌شود (`Worker port is occupied; start refused.`) | فرایند مزاحم را ببندید؛ پورت‌های worker در بازهٔ ۴۱۰۰۰ به بالا انتخاب می‌شوند |
| قفل SQLite | WAL و `busy_timeout=15000` احتمال قفل را کم می‌کند؛ در قفل طولانی، نوشتن تصویر/ثبت ردیف می‌تواند شکست بخورد | آنتی‌ویروس/همگام‌ساز شبکه را از پوشهٔ داده دور کنید؛ فضای دیسک را بررسی کنید. `-wal`/`-shm` را دستی پاک نکنید |
| دیسک پر | نوشتن فایل یا ثبت SQL شکست می‌خورد و Job ممکن است `failed`/`partial` شود | فضا آزاد کنید؛ خروجی‌های تأییدشده را پس از بک‌آپ پاک کنید |
| نشست منقضی | jobها ممکن است `failed` یا `unknown` شوند | از `/accounts/<id>/login` دوباره دستی وارد شوید و Save بزنید |
| دروازه در حلقهٔ crash | backoff به ۱۸۰۰ ثانیه می‌رسد و پس از ۸ تلاش خودکار متوقف می‌شود (Start دستی وضعیت را پاک می‌کند) | نصب، Chrome، patchright و لاگ worker را بررسی کنید؛ پس از رفع، worker را دوباره Start کنید |

## ۳. پایش با `/api/health`

این endpoint بدون احراز هویت و فقط روی loopback پاسخ می‌دهد و **فقط شمارنده و بولین** برمی‌گرداند؛ هیچ path، prompt، نام حساب، API key یا cookie در آن نیست:

```powershell
Invoke-RestMethod http://127.0.0.1:8081/api/health
```

```json
{
  "ok": true,
  "version": "0.6.0",
  "gateway_installed": true,
  "accounts": 2,
  "running_workers": 2,
  "dispatch_paused": false,
  "queued": 3,
  "running": 1,
  "completed": 140,
  "partial": 2,
  "failed": 1,
  "unknown": 0
}
```

| فیلد | معنا | نشانهٔ هشدار |
|---|---|---|
| `ok` | پاسخ سرویس برقرار است | اگر پاسخ نداد، فرایند Uvicorn یا پورت را بررسی کنید |
| `version` | نسخهٔ FastAPI/اپلیکیشن | اگر `0.6.0` نیست، سورس/فرایند قدیمی اجراست |
| `gateway_installed` | وجود `vendor/gpt-web-gateway/src/server.js` و `node_modules` و Node | `false` یعنی workerها بالا نمی‌آیند |
| `accounts` | تعداد حساب‌های ثبت‌شده | افت ناگهانی یعنی حذف/بازیابی داده |
| `running_workers` | فرایندهای زندهٔ worker | کمتر از حساب‌های autostart = worker مرده یا در حال restart |
| `dispatch_paused` | توقف سراسری ارسال | `true` ناخواسته یعنی Jobها در صف می‌مانند |
| `queued` | Jobهای منتظر | رشد پیوسته بدون کاهش یعنی upstream/worker مشکل دارد |
| `running` | Jobهای در حال اجرا | ماندن طولانی روی یک عدد یعنی Job گیرکرده یا worker بی‌پاسخ |
| `completed` / `partial` / `failed` / `unknown` | شمارندهٔ تجمعی وضعیت‌ها | رشد `unknown` یا `failed` نیازمند بررسی دستی است |

این endpoint یک health check سطحی است: workerها و نشست ChatGPT را probe نمی‌کند. برای بررسی نصب از `python scripts/doctor.py` و برای وضعیت هر حساب از پنل استفاده کنید. در پایش خودکار، `gateway_installed=false`، `running_workers < accounts`، رشد پایدار `queued` و افزایش `unknown` را هشدار در نظر بگیرید.

## ۴. کارهایی که نباید انجام داد

- **دو فرایند Uvicorn** یا `--workers` بیشتر از ۱ روی یک دیتابیس/پورت‌ها اجرا نکنید. scheduler و قفل‌ها در حافظه‌اند؛ نتیجه Job تکراری، پورت اشغال و دادهٔ ناسازگار است.
- `--reload` را با Job فعال اجرا نکنید.
- پنل، دروازهٔ worker یا پورت‌ها را عمومی نکنید. تنها `127.0.0.1` مجاز است؛ برای دسترسی بیرونی TLS، reverse proxy، فایروال و احراز هویت لازم است.
- از **حساب اصلی ChatGPT** استفاده نکنید. حساب آزمایشی جدا و مجاز بسازید.
- برای عبور از محدودیت، حساب عوض نکنید، CAPTCHA/MFA را دور نزنید و درخواست را تکرار خودکار نکنید.
- Jobهای `unknown` را دوباره ارسال نکنید؛ اول نتیجهٔ upstream را دستی بررسی کنید.
- فایل‌های `hub.db-wal` یا `hub.db-shm` را در زمان اجرا پاک نکنید و ردیف‌های صف را دستی ویرایش/حذف نکنید.
- تنظیمات Gateway (`gateway_max_tabs`, `max_batch_images`) را بدون Stop/Start کردن worker خالی انتظار اعمال فوری نداشته باشید؛ Job فعال را هم قطع نکنید.
- فقط `hub.db` را در زمان اجرا به‌عنوان بک‌آپ کپی نکنید؛ WAL و پوشهٔ `gallery` را هم بگیرید.
- چند replica یا PostgreSQL را «پشتیبانی‌شده» فرض نکنید؛ این موارد در v0.6 **تأییدنشده** هستند.

## ۵. نگهداری دوره‌ای

- روزانه: `/api/health`، صف، `unknown`/`failed`، فضای دیسک.
- هفتگی: doctor، اندازهٔ `data/gallery`، `data/previews`، `data/exports` و لاگ workerها؛ بک‌آپ کامل.
- پس از هر ارتقا: doctor، یک Job کوچک، بررسی گالری/ZIP و پایش چند دقیقه‌ای.
- برای بازنشانی نشست/worker: ابتدا Stop از Accounts (تا supervisor آن را احیا نکند)، سپس Start. برای تغییر تنظیمات Gateway همین روش در worker خالی توصیه می‌شود.
- ترتیب خاموش‌کردن: Pause ارسال → انتظار برای Jobهای فعال → Ctrl+C روی Uvicorn → بک‌آپ. ترتیب روشن‌کردن: doctor → Uvicorn → بررسی `/api/health` و Accounts.

برای نصب و اولین اجرا [FIRST_RUN_FA.md](FIRST_RUN_FA.md) و برای شروع سریع [START_HERE_FA.md](../START_HERE_FA.md) را ببینید.

## workers روی تقاضا (پیش‌فرض v0.6)

rule اصلی: **مرورگر فقط وقتی کار هست باز می‌ماند.** تنظیم زنده: Operations → «Idle browser shutdown (min)» یا `PATCH /api/control` با `idle_worker_shutdown_minutes` (۰ = خاموش‌کردن قابلیت، یعنی همیشه باز).

- اگر یک اکانت به مدت N دقیقه نه Job داشته باشد و نه کسی صفحهٔ Login را باز کرده باشد، worker (Node + Chrome + Xvfb) متوقف می‌شود: **رم آزاد می‌شود**.
- با اولین Job جدید (یا باز کردن صفحهٔ Login) خودکار بیدار می‌شود. سشن از `data/accounts/<id>/gateway/auth/session.json` بازیابی می‌شود؛ پس Save Session را جدی بگیر.
- حسابی که **دستی Stop** شده بیدار نمی‌شود؛ Jobش در صف می‌ماند تا خودت Start کنی (عمداً، تا Stop اپراتور را زیر پا نگذاریم).
- تلاش‌های بیدارباش با backoff (۲۰ ثانیه تا ۵ دقیقه) انجام می‌شود؛ یک نصب خراب باعث storm پروسه نمی‌شود.
- اگر پنل با `kill -9`/taskkill بمیرد، gateway یتیم روی پورت می‌ماند؛ شروع بعدی فقط اگر آن پروسه **متعلق به همان حساب** باشد آن را می‌بندد (درخت پروسه هم کامل بسته می‌شود، نه فقط Node).
- معیارها: `/api/health` حالا `running_workers`, `idle_workers` و `worker_policy` را هم می‌دهد.

**اثبات زنده (این ماشین):** idle=1 → پس از ۳۷ ثانیه `running_workers=0, idle_workers=1`؛ سپس یک Job → worker خودکار بیدار شد و Job `completed` با ۱ تصویر. مصرف رم در حالت idle فقط پنل است (~۲۵۰MB).


## مقیاس ۱۰ اکانت / ۱۰ تولید هم‌زمان

هدف تأییدشدهٔ v0.6: ۱۰ اکانت و ۱۰ Job هم‌زمان روی یک فرایند.

| تنظیم | مقدار پیشنهادی | چرا |
|---|---|---|
| `max_running_accounts` | 10 | سقف workerهای زنده |
| `global_max_inflight` | 10 (تا 32) | استخر ۳۲ رشته‌ای با سقف هم‌تراز شده |
| `gateway_max_tabs` | 1 | هر تب یک صفحهٔ ChatGPT سنگین است |
| `parallel_limit` (هر اکانت) | 1–2 | جمع آن نباید از سقف کلی بیشتر شود |
| `idle_worker_shutdown_minutes` | 10–15 | بین دسته‌ها رم آزاد می‌شود |
| `min_free_memory_mb` | 800 | شروع worker جدید در رم کم را رد می‌کند (به‌جای OOM) |
| RAM سرور | ~16GB | ۱۰ × ۰.۸–۱.۳GB + پنل |
| CPU | ۸ هسته به بالا | رندر Chrome |
| دیسک | ~۳–۵GB برای ۱۰۰۰ تصویر | اصل PNG + preview |

پایش: `GET /api/health` هر ۱۰ ثانیه؛ اگر `idle_workers` بالا رفت طبیعی است (workers روی تقاضا). اگر `queued` رشد کرد و `running_workers` صفر ماند: `doctor.py` و `worker.log` را ببین.

**ویدیو:** این خط لوله فقط تصویر می‌سازد؛ برای ویدیو باید یک adapter ارائه‌دهندهٔ جدید اضافه شود (نقطهٔ تعویض: `hub/gateway.request_generation`).


## نگهداری خودکار، گارد دیسک و retention (v0.7)

یک tick ساعتی در scheduler (بعد از refresh سلامت و قبل از dispatch) این کارها را انجام می‌دهد:

| تنظیم | پیش‌فرض | رفتار |
|---|---|---|
| `preview_retention_days` | 30 | حذف preview های WebP قدیمی‌تر از N روز؛ `0` = خاموش. **اصنل‌ها هرگز حذف نمی‌شوند** |
| `export_retention_hours` | 24 | حذف ZIP های موقت قدیمی‌تر از N ساعت؛ `0` = خاموش |
| `worker_log_max_mb` | 20 | چرخش لاگ worker **متوقف** (به `worker.log.1`)؛ لاگ worker در حال اجرا دست نمی‌خورد. `0` = خاموش |
| `min_free_disk_mb` | 500 | اگر فضای آزاد کمتر شود، **dispatch جدید متوقف** می‌شود؛ Jobهای در صف می‌مانند و هیچ داده‌ای حذف نمی‌شود. `0` = خاموش |

- فایل‌های `.incoming-*` قدیمی‌تر از ۱ ساعت (spool نیمه‌کاره) پاک می‌شوند.
- هر حذف/چرخش در audit به‌صورت **فقط شمارش** ثبت می‌شود (بدون مسیر/پرامپت/راز).
- `GET /api/health` حالا `disk_free_mb`, `disk_low`, `unresolved_unknown` و `rate_limited` را هم می‌دهد.
- هشدار دیسک کم در Operations (بخش capacity) و در نوار وضعیت Overview هم دیده می‌شود.

## آشتی Jobهای `unknown` (بدون retry)

`unknown` یعنی «نمی‌دانیم upstream انجام شد یا نه». تا v0.7 فقط می‌شد حذف کرد؛ حالا:

```
POST /api/jobs/{id}/resolve
X-CSRF-Token: <csrf>
{"resolution": "delivered_manually", "note": "در تاریخچهٔ چت پیدا شد"}
```

| resolution | معنا | کار بعدی |
|---|---|---|
| `delivered_manually` | تصویر واقعاً ساخته شده | کاری نکن؛ دو بار نساز |
| `not_delivered` | چیزی ساخته نشده | اگر لازم است، آگاهانه دوباره submit کن |
| `abandoned` | رها کردن | رکورد برای حسابرسی می‌ماند |

- فقط روی Job با `status="unknown"` مجاز است؛ بقیه `409`.
- یک‌بارمصرف است؛ دوباره `409`.
- وضعیت Job عوض نمی‌شود (`unknown` می‌ماند)، فقط `resolution`, `resolved_at`, `resolution_note` ثبت می‌شود.
- در UI: Jobs → فیلتر Problems → دکمهٔ **Resolve** روی همان ردیف.

## بکاپ و بازیابی

```bash
python scripts/backup.py create --dest /var/backups/ninja-image-hub   # snapshot + manifest
python scripts/backup.py verify --path /var/backups/ninja-image-hub/<ts>
python scripts/backup.py restore --path ... --target /path/to/data --dry-run
python scripts/backup.py restore --path ... --target /path/to/data    # پنل را قبلش متوقف کن
```

- دیتابیس با API بکاپ SQLite (سازگار با WAL) snapshot می‌شود؛ کپی خام `hub.db` در حالت WAL کافی **نیست**.
- شامل: `hub.db`, origginalهای `gallery/`, `accounts/<id>/auto-login.json`, `gateway/auth/` و `.env` (mode 0600).
- شامل نمی‌شود: previews و exportهای موقت (قابل بازتولید)، مگر `--include-derived`.
- `verify` همهٔ hash ها را چک می‌کند؛ `restore` روی مقصد غیرخالی فقط با `--force`.
- بکاپ شامل **سشن ChatGPT و رازهای `.env`** است: رمزنگاری‌شده و خصوصی نگه دار.

Timer های آماده در `deploy/`:

```bash
sudo cp deploy/ninja-backup.service deploy/ninja-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now ninja-backup.timer
# watch (اختیاری، webhook):
sudo cp deploy/ninja-watch.service /etc/systemd/system/
sudo install -m 0600 /dev/null /etc/ninja-watch.env
sudo nano /etc/ninja-watch.env   # NINJA_WEBHOOK_URL=... و در صورت نیاز NINJA_ADMIN_PASSWORD=...
sudo systemctl enable --now ninja-watch
```

## هشدارها

```bash
python scripts/watch.py --once --panel http://127.0.0.1:8081   --webhook https://hooks.example/alert --unresolved-min 1   --disk-min-mb 500 --queued-age-min 15
```

- `--once` برای cron/systemd-timer؛ خروج `1` یعنی هشدار تازه دارد.
- بدون `NINJA_ADMIN_PASSWORD` فقط health خوانده می‌شود (بدون سن صف).
- هشدارها با state file تکرار نمی‌شوند (`--repeat-min`).
- payload: `{"text": "...", "alerts": [...], "health": {...}}` — برای Slack/Telegram یک آداپتور کوچک کافی است.

## اندازه‌گیری ظرفیت واقعی (۱۰ اکانت)

```bash
python scripts/capacity_report.py --password "$ADMIN_PASSWORD"
python scripts/capacity_report.py --password "$ADMIN_PASSWORD" --submit --yes --accounts 10
```

- حالت اول فقط snapshot می‌دهد: workerها، RAM، صف، سقف‌ها، دیسک، unknown حل‌نشده.
- حالت دوم به تعداد `--accounts` × `--jobs-per-account` Job واقعی می‌فرستد (مصرف سهمیه!) و اندازه می‌گیرد:
  wall time، تصاویر ذخیره‌شده، پایداری تصویر/ساعت، انتظار صف p50/p95 (از analytics) و تولید p50/p95.
- **قاعده:** هر عددی که این ابزار می‌دهد فقط برای همان اجرا معتبر است؛ آن را به «۵۰۰ تصویر در ۵ ساعت» تعمیم نده مگر اندازه‌گیری همان بازه همین را نشان دهد.

## پلن و اشتراک اکانت (Plus / Free و تاریخ انقضا)

از v0.7.1 پنل خودش پلن واقعی هر اکانت را از **API داخلی ChatGPT** (همان داده‌ای که صفحهٔ Subscription نشان می‌دهد) می‌خواند؛ نه حدس و نه برچسب دستی.

### چه چیزی خوانده می‌شود
| فیلد ChatGPT | معنی |
|---|---|
| `plan_type` / `plan_display_name` | `guest`/`Free`، `plus`/`Plus`، `pro`، `team` … |
| `subscription_plan` | `chatgptguestplan`، `chatgptplusplan` … |
| `has_active_subscription` | آیا اشتراک فعال است |
| `expires_at` | پایان واقعی اشتراک (اگر وب بدهد) |
| `renews_at` | تاریخ تمدید بعدی |
| `cancels_at` | تاریخ لغو/پایان برنامه‌ریزی‌شده |
| `will_renew` | آیا تمدید خودکار فعال است |
| `has_previously_paid_subscription` | آیا قبلاً پول داده |

### در پنل کجا دیده می‌شود
- کارت اکانت: `Plan: PLUS · 12d left · renews` یا هشدار **Not Plus** / **Expiring** (≤۳ روز).
- دیالوگ ویرایش اکانت: **Detected plan** + دکمهٔ **Refresh plan from ChatGPT**.
- `GET /api/accounts/<id>/plan` → خواندن و ذخیرهٔ فوری.
- `GET /api/health` → شمارنده‌های `accounts_free_plan` و `accounts_plan_expiring` (بدون نام/ایمیل).
- هر ۶ ساعت به‌صورت خودکار برای workerهای روشن تازه می‌شود.

### تفسیر درست تاریخ‌ها
- **اشتراک فعال:** `renews_at` تاریخ تمدید است؛ «چند روز مانده» فقط وقتی معنی دارد که `expires_at` یا `cancels_at` وجود داشته باشد.
- **لغو شده:** `cancels_at` = روزی که Plus تمام می‌شود؛ `days_left` از همان محاسبه می‌شود.
- **Free/مهمان:** همهٔ تاریخ‌ها `null` هستند؛ یعنی «اشتراک فعالی نیست»، نه «امروز منقضی شد».
- `session_expires` مربوط به **کوکی ورود مرورگر** است، نه اشتراک — با تاریخ اشتراک اشتباه نشود.

> محدودیت‌ها: این داده از «حساب/ورک‌اسپیس شخصی» می‌آید؛ برای workspaceهای تیمی ساختار متفاوت است. اگر اکانت با ایمیل دیگری Plus داشته باشد، این حساب Free نشان داده می‌شود. برای اطمینان، خروجی پنل را با صفحهٔ **Settings → Subscription** مقایسه کن.


## سطح تلاش مدل (Thinking effort) برای تصاویر — v0.8

ChatGPT در UI یک کنترل «Thinking effort» دارد. مقدار پیش‌فرض/قبلیِ آن روی هر چت می‌تواند
فرق کند؛ ما **برای هر درخواست تصویر** صریحاً همان سطح تنظیم‌شده را می‌فرستیم (پیش‌فرض **High**)
و از خروجی Gateway می‌خوانیم که UI واقعاً چه چیزی را اعمال کرده است.

| تنظیم | مقدار | معنی در Gateway |
|---|---|---|
| `thinking_effort` | `high` (پیش‌فرض) | `extended` → «High» |
| | `extra_high` | `extra_high` (اگر پلن اجازه دهد) |
| | `medium` | `standard` |
| | `instant` | `instant` |
| | `pro` | `pro` |

هر Job این فیلدها را نگه می‌دارد و در ستون وضعیت Jobs نشان می‌دهد:

| فیلد | معنی |
|---|---|
| `thinking_requested` | چیزی که خواستیم (`high`) |
| `thinking_applied` | چیزی که UI واقعاً اعمال کرد |
| `thinking_verified` | `1` تأییدشده، `0` تأیید نشد، `null` بدون گزارش (Gateway قدیمی) |
| `thinking_fallback` | سطح جایگزینی که Gateway اعلام کرده |
| `thinking_mixed` | در یک batch همهٔ تصاویر روی یک سطح نبودند |

نشانه‌ها در UI: `High ✓` (تأییدشده)، `High → Medium` (افت سطح)، `High · mixed`، `High ?` (نامعلوم).

**سیاست در بدترین حالت‌ها**

- **پلن High را ندارد یا سهمیه Pro تمام شده:** Gateway خودش سطح جایگزین را گزارش می‌کند؛
  تصویرِ سالم **حفظ می‌شود**، job شکست نمی‌خورد و هیچ retry ای انجام نمی‌شود (چون retry هزینهٔ
  دوباره و ریسک تصویر تکراری دارد). فقط badge + شمارندهٔ هشدار ثبت می‌شود.
- **Gateway قدیمی/بدون echo:** `thinking_verified = null` → `High ?` و در ۲۴ ساعت شمرده می‌شود؛
  هرگز به‌عنوان موفقیت نمایش داده نمی‌شود.
- **batch با سطوح مختلف:** `thinking_mixed=1` و badge «mixed».
- **مقدار نامعتبر در `.env`:** به High برمی‌گردد (هرگز به سطح پایین‌تر fallback نمی‌کند).

پایش:

```bash
curl -s http://127.0.0.1:8081/api/health | python -m json.tool | grep effort
python scripts/watch.py --once   # هشدار effort_downgrade اگر در ۲۴h افت ثبت شده باشد
```

اگر می‌خواهی بدون افت کار کنی، فقط اکانتی را وصل کن که High را در همان چت نشان می‌دهد؛ و
پس از هر تغییر UI توسط OpenAI، یک Job آزمایشی بفرست و `thinking_verified` را ببین.

### پلن workspace (Business / Pro) در برابر پلن شخصی

ChatGPT دو لایهٔ متفاوت دارد و ممکن است یکی Free باشد و دیگری پولی:

| لایه | مثال | منبع |
|---|---|---|
| حساب شخصی | `plan_type: guest` (Free) | `GET /backend-api/accounts/check` |
| **workspace فعال** | `Plan: Business` | صفحهٔ **Settings → Account** |

اگر اکانت شخصی Free باشد ولی کاربر داخل یک workspace پولی باشد، پنل هر دو را بررسی می‌کند:
اول دادهٔ API حساب را می‌خواند؛ اگر Free/Guest بود، صفحهٔ Settings → Account را از داخل همان مرورگر باز می‌کند و پلن workspace (مثلاً **Business**) را می‌خواند و آن را ترجیح می‌دهد. شناسهٔ workspace هم از همان صفحه خوانده می‌شود و در دیالوگ جزئیات نمایش داده می‌شود.

- فیلدهای `source` و `workspace_plan` می‌گویند پلن از کدام لایه آمده است.
- **تاریخ تمدید برای عضو workspace معمولاً نمایش داده نمی‌شود** (فقط مالک/ادمین آن را می‌بیند)؛ در این حالت پنل صریح می‌نویسد «managed by the workspace — not exposed to members» و هیچ تاریخی جعل نمی‌کند.
- صفحهٔ Settings برای هر حساب فقط یک‌بار در چرخهٔ تازه‌سازی (۶ ساعته) خوانده می‌شود؛ دکمهٔ **Refresh plan** هم آن را فوری اجرا می‌کند.

## بررسی هویت هنگام لاگین (ایمیل و workspace)

بعد از هر ورود — دستی، ریموت یا **لاگین خودکار** — پنل هویت واقعی مرورگر را می‌خواند:

- **ایمیل واردشده** از صفحهٔ Settings → Account
- **شناسهٔ workspace فعال**
- زمان آخرین بررسی

و با آنچه برای این اکانت انتظار داریم مقایسه می‌کند (ایمیل ذخیره‌شده در لاگین خودکار، یا ایمیل‌لیبل اکانت):

| وضعیت | معنی |
|---|---|
| `verified` | ایمیل همان است که انتظار داشتیم |
| `confirmed` | اپراتور صریحاً تأیید کرده «همین اکانت درست است» |
| `mismatch` | ایمیلِ واردشده با انتظار فرق دارد → باید تأیید یا اصلاح شود |
| `workspace_changed` | workspace فعال عوض شده (مثلاً از Business به Personal) |
| `unknown` | هنوز بررسی نشده |

### چه زمانی خودکار بررسی می‌شود؟
1. بلافاصله بعد از **Save Session** در صفحهٔ لاگین ریموت.
2. بعد از هر **Start** ورکر (شامل لاگین خودکار).
3. در چرخهٔ تازه‌سازی هر ۶ ساعت و با دکمهٔ **Verify now**.

### اگر ایمیل فرق داشت چه کنیم؟
دیالوگ **Details → Identity** ایمیل و workspace واقعی را نشان می‌دهد و دو دکمه دارد:
- **Verify now** — خواندن دوبارهٔ هویت از مرورگر.
- **This is the right account** — تأیید صریح اپراتور (وضعیت `confirmed`).

اگر `mismatch` دیدی یعنی ورکر با اکانت دیگری وارد شده؛ از مسیر لاگین خارج شو (Open ChatGPT → خروج) و با اکانت درست وارد شو، سپس دوباره Verify بزن. برای اکانت‌های لاگین خودکار، ایمیل ذخیره‌شده مبنای مقایسه است؛ اگر رمز/ایمیل عوض شده، اول Remove credentials و سپس مقادیر جدید را ذخیره کن.


## تصاویر مرجع (Reference images) — v0.9

هر Job می‌تواند تا **۱۰ تصویر مرجع** همراه پرامپت داشته باشد. این تصاویر در همان چت
ChatGPT به‌عنوان attachment کنار پرامپت گذاشته می‌شوند و خروجی از آن‌ها الگو می‌گیرد.

### جریان کار

1. در **Image Studio** تصاویر را drag/drop یا انتخاب کن (PNG/JPEG/WebP).
2. مرورگر اول `POST /api/uploads` (multipart، فیلد `files`) را می‌فرستد؛ خروجی `upload_id` است.
3. سپس `POST /api/generate` با همان `upload_id`؛ Job ساخته می‌شود و فایل‌ها تا لحظهٔ اجرا در
   `data/uploads/<uuid>/` (mode 0600) می‌مانند.
4. هنگام dispatch، worker آن‌ها را به‌صورت **multipart `reference_images`** به Gateway می‌دهد.
5. با terminal شدن Job، پوشهٔ upload حذف می‌شود؛ برای `unknown` می‌ماند تا retention.

### محدودیت‌ها (سه‌گانه: OpenAI + ChatGPT + Gateway)

| محدودیت | مقدار پیش‌فرض | منشأ |
|---|---|---|
| تعداد فایل | ۱۰ | سقف ورودی Images API و Gateway |
| فرمت | PNG / JPEG / WebP | فرمت‌های پذیرفته‌شدهٔ Images API |
| حجم هر فایل | ۲۰ MiB | سقف آپلود کامپوزر ChatGPT |
| حجم کل | ۵۰ MiB | پاکت بافرشدهٔ Gateway (RAM) |
| پیکسل هر تصویر | ۱۶٬۷۷۷٬۲۱۶ | سقف ورودی تصویر در API |
| animated | رد می‌شود | تصویر متحرک مرجع پذیرفته نیست |
| PDF/DOCX/… | رد می‌شود (415) | ورودی تصویر فقط image است؛ فایل متنی مرجع نیست |

همهٔ این‌ها زنده در Operations قابل تغییرند (`upload_max_files`, `upload_max_file_mb`,
`upload_max_total_mb`, `upload_retention_hours`) و سمت سرور اعمال می‌شوند — نه فقط در مرورگر.

### اعتبارسنجی و امنیت

- نوع فایل با **magic bytes** و decode واقعی تعیین می‌شود؛ پسوند و Content-Type مرورگر قابل اعتماد نیستند.
- فایل‌ها هرگز re-encode/resize نمی‌شوند؛ bytes اصلی ذخیره و ارسال می‌شوند، همراه sha256.
- نام فایل برای نمایش sanitize می‌شود؛ محتوای تصاویر لاگ نمی‌شود.
- مسیرهای آپلود با session admin + CSRF (یا Bearer agent) محافظت می‌شوند.
- اگر دیسک کم باشد (`disk_low`)، آپلود با 507 رد می‌شود.
- آپلود در بکاپ گنجانده نمی‌شود (موقت است) و retention آن را پاک می‌کند.

### خطاهای رایج

| نشانه | معنا / کار |
|---|---|
| `413` | حجم فایل یا کل بیش از سقف؛ یا هدر Content-Length از سقف middleware بزرگ‌تر است |
| `415` | فرمت پشتیبانی‌نشده (SVG/PDF/HEIC/…) |
| `422` | تصویر خراب، متحرک، >۱۶MP، یا `upload_id` منقضی/ناموجود |
| `409` (DELETE) | upload هنوز توسط Job در صف/در حال اجرا استفاده می‌شود |
| Job `failed` با «expired before dispatch» | فایل‌ها پیش از dispatch پاک شده‌اند؛ **هیچ درخواستی** به provider نرفت |
| Job `unknown` | ریسک upstream؛ مرجع‌ها برای بازبینی می‌مانند تا retention |


## بازبینی عمیق جریان آپلود — v0.9.1

پس از ریلیز v0.9، کل مسیر آپلود دوباره بازبینی شد و این نقص‌ها بسته شدند:

| یافته | اصلاح |
|---|---|
| کل درخواست آپلود در RAM نگه داشته می‌شد (تا ۵۰MB × همزمانی) | حالا chunk-by-chunk روی دیسک نوشته می‌شود، همزمان sha256 محاسبه می‌شود؛ حافظهٔ پنل تقریباً ثابت |
| درخواست chunked (بدون Content-Length) می‌توانست از سقف هدر عبور کند و موقتاً دیسک را پر کند | برای مسیر آپلود `Content-Length` اجباری شد؛ نبودش `411` |
| پوشهٔ آپلود 0755 بود | `0700` برای پوشه و `0600` برای فایل‌ها و `meta.json` |
| دو فایل با نام یکسان در multipart نام تکراری می‌گرفتند | نام امن یکتا: `1-name.png`, `2-name.png`؛ نام اصلی اپراتور در metadata می‌ماند |
| لغو Job در صف، حذف Job و purge اکانت، پوشهٔ upload را باقی می‌گذاشت | همهٔ این مسیرها upload را آزاد می‌کنند |
| پوشه‌های `.incoming-*` نیمه‌کاره فقط با retention (۲۴h) پاک می‌شدند | حالا بعد از ۱ ساعت، مستقل از retention |
| `meta.json` دست‌کاری‌شده می‌توانست نام فایل خارج از پوشه بدهد | نام فایل هنگام load با الگوی سخت `<n>-<sha12>.<ext>` اعتبارسنجی می‌شود |
| UI، فایل‌های هم‌نام/هم‌اندازه با محتوای متفاوت را تکراری می‌دید | `lastModified` هم در تشخیص تکراری لحاظ می‌شود |
| فیلد drop zone در حالت keyboard فوکوس دیده نمی‌شد | حلقهٔ فوکوس `:focus-within` |
| کمبود تست برای سقف مجموع، جریان agent، پاک‌سازی‌ها، مجوز فایل | ۹ تست جدید (مجموع ۲۰ تست upload) |

نکته: محدودیت‌های رسمی تغییر نکرده‌اند (۱۰ فایل، ۲۰MiB هر فایل، ۵۰MiB مجموع، PNG/JPEG/WebP، ۱۶MP).


## بهینه‌سازی جریان تصاویر مرجع — v0.10

### اندازه‌گیری واقعی روی همین ماشین

| مرحله | قبل | بعد |
|---|---|---|
| تایپ پرامپت ۳٬۱۲۶ کاراکتری همراه مرجع | **۹۵٬۵۳۶ ms** | **۷۵۷ ms** (patch `insertText`) |
| کل درخواست Gateway (همان Job) | **۱۴۴.۹ s** | **۷۴.۰ s** |
| زمان Job از submit تا پایان | **۱۴۹.۸ s** | **۷۸.۹ s** |
| تب‌های مرجع ۱۰×۴MiB در مرورگر | ۱۰ decode کامل (حافظهٔ بالا) | ۱۰ thumbnail ۹۶px؛ JS heap ~۱MiB |
| آپلود ۴۰MiB (۱۰ فایل) | ۵۵۶ ms، RSS پنل +۰.۱MiB | همان (استریم روی دیسک) |

نکتهٔ مهم: ChatGPT وقتی attachment دارد، `fill()` را قبول نمی‌کند؛ upstream مجبور است
کاراکتر‌به‌کاراکتر تایپ کند (۱۰ms delay + round-trip CDP ≈ ۳۰ms/char). برای پرامپت‌های
بلند این تنها گلوگاه غالب بود. Patch فقط وقتی مرجع هست فعال می‌شود و fail-open است.

### اقدام لازم بعد از آپدیت

هر worker را یک‌بار **Stop → Start** کن تا `ensure_worker_copy` پچ جدید را روی workspace
اعمال کند (پچ idempotent است و در لاگ `[fast-insert]` دیده می‌شود).

### فرصت‌های بعدی (اندازه‌گیری/پیاده‌سازی نشده)

| ایده | سود تقریبی | هزینه/ریسک |
|---|---|---|
| کوچک‌کردن مرجع در مرورگر قبل از آپلود (اختیاری، ۲۰۴۸px / WebP) | آپلود ۴۰MiB → ۲–۵MiB؛ RAM مرورگر و Node کمتر | تغییر bytes مرجع؛ باید opt-in باشد |
| پچ Gateway برای diskStorage به‌جای multer memoryStorage | کاهش RAM Node تا ~۵۰MB در هر Job | پچ بزرگ‌تر روی vendor؛ ریسک بیشتر |
| کتابخانهٔ مراجع (استفادهٔ مجدد بین Jobها) | حذف آپلود تکراری هنگام تکرار یک ایده | نیاز به retention/UI جدید |
| آپلود موازی فایل‌ها در شبکهٔ پرتأخیر | سرعت بیشتر در اینترنت واقعی | چند request + ادغام سمت سرور |
| attach بعد از fill (به‌جای type) | حذف کامل مسیر تایپ | نیاز به تأیید React؛ شکننده‌تر از patch فعلی |

### محدودیت‌ها

- پچ به نسخهٔ پین‌شدهٔ Gateway (v2.16.11) وابسته است؛ اگر anchor تغییر کند patch بی‌اثر
  می‌شود و رفتار قبلی (تایپ کند) برمی‌گردد — بدون خرابی.
- اندازه‌گیری‌ها تک‌اجرایی‌اند و زمان تولید تصویر خودش متغیر است؛ اعداد تایپ دقیق‌اند
  (از لاگ خود worker)، اعداد کل تقریبی.

## تشخیص «Image generation failed» در ChatGPT

گاهی ChatGPT خودش تصویر را نمی‌سازد و در همان نوبت کارت **Image generation failed** با دکمهٔ **Try again** نشان می‌دهد — در حالی که ممکن است یک تصویر قدیمی‌تر یا کارت ناقص هم روی صفحه دیده شود.

از v0.10.1 gateway این حالت را **قبل از شمردن تصویر** تشخیص می‌دهد:

- متن نوبت آخر دستیار برای عبارت‌های «image generation failed» / «generation failed» (و معادل روسی) بررسی می‌شود؛ اگر تازه باشد، نتیجه `generation_failed` است.
- این تشخیص بر سیگنال «تصویر جدید» اولویت دارد، بنابراین تصویر کهنه/ناقص به‌عنوان موفقیت تحویل داده نمی‌شود.
- در انتظارهای تطبیقی (adaptive timeout) هم همین متن دیده شود، سریع شکست می‌خورد (بدون صبر تا پایان بودجه).
- خروجی: خطای typed با پیام «ChatGPT reported "Image generation failed" — no usable image was produced…» و کد `image_generation_failed` (HTTP 422).

در پنل، Job با وضعیت **failed** و همان پیام در ستون OUTPUT/آخرین وضعیت دیده می‌شود. هیچ retry خودکاری انجام نمی‌شود (طبق قرارداد: فقط عدم‌قطعیت خودکار نیست؛ خطای تولید هم بدون اجازه تکرار نمی‌شود). راه‌حل: در همان چت **Try again** را بزن یا Job را با همان prompt دوباره ثبت کن.

> پس از هر آپدیت، workerها را Stop → Start کن تا وصله روی کپی gateway اعمال شود (patch idempotent و guarded است).


## بهینه‌سازی دور دوم — v0.11

### ۱) انتظار رندر: از ۱۵ ثانیه ثابت به سقف قابل تنظیم

Upstream بعد از تشخیص تصویر، ۱۵ ثانیه ثابت صبر می‌کرد (`RENDER_WAIT_MS`). یک پچ adaptive
نوشتیم که منتظر کامل‌شدن `<img>` تمام‌اندازه بماند؛ در اجرای واقعی هر بار به سقف ۱۵s می‌خورد،
چون تصویر از مسیر backend/canvas خوانده می‌شود و در DOM به‌صورت `<img>` بزرگ و پایدار
ظاهر نمی‌شود. بنابراین پچ حذف شد و به‌جایش:

- `render_wait_sec` (پیش‌فرض **۸**، بازه ۳–۶۰) به worker پاس می‌شود (`RENDER_WAIT_SEC`).
- مسیر استخراج خودش تا آماده‌شدن تصویر polling می‌کند، پس این عدد فقط **سقف** است.
- تست زنده: با `render_wait_sec=5` یک Job مرجعی واقعی در **۵۳.۹۹s** کامل شد
  (`Waiting 5s...` سپس `Canvas extraction successful`)، در حالی که با ۱۵s همان Jobها
  ۵۷.۹–۷۳.۹s بودند (زمان تولید مدل هم متغیر است).
- بعد از تغییر تنظیم، worker را Stop/Start کن (env در استارت خوانده می‌شود).

### ۲) کوچک‌سازی اختیاری مرجع در مرورگر (opt-in)

در Image Studio یک تیک «Optimize large references» اضافه شد:

- فعال بودن: تصاویری که ضلع بزرگ‌شان > ۲۰۴۸px یا حجم‌شان > ۲.۵MiB است، با
  `createImageBitmap` + canvas به WebP (کیفیت ۰.۸۵، حداکثر ۲۰۴۸px) تبدیل می‌شوند —
  فقط اگر خروجی کوچک‌تر از فایل اصلی باشد.
- **پیش‌فرض خاموش** است (opt-in): با خاموش بودن، همان فایل انتخاب‌شده byte-for-byte آپلود می‌شود.
- اندازه‌گیری زنده: دو PNG ۴.۱MiB → دو WebP **۱.۰MiB** (جمع ۸.۲ → ۲.۱MiB)؛ Job در صف
  با `refs=2` و فایل‌های `image/webp` در `data/uploads` تأیید شد.
- سمت سرور هیچ re-encode/resize انجام نمی‌دهد و محدودیت‌های رسمی همان‌ها هستند.

### ۳) مواردی که عمداً پیاده نشدند

| مورد | دلیل |
|---|---|
| multer → diskStorage در Gateway | نیاز به تغییر ساختاری مسیر buffer→path در generate و edit، ریسک رگرسیون بالا؛ سود آن فقط RAM نودی است که با سقف ۵۰MiB کنترل شده |
| کتابخانهٔ مراجع | نیاز به retain/reuse + UI انتخاب؛ در backlog |
| fill-then-attach | ممکن است React متن را با attach پاک کند؛ با insertText فعلی تایپ زیر ۱s است و سود حاشیه‌ای کم |


## طبقه‌بندی خطا، ابزار آشتی و بررسی پچ‌ها — v0.12

### خطاهای Gateway حالا از body خوانده می‌شوند

| error_kind | نتیجه | چرا |
|---|---|---|
| `page_load_failed`, `login_failed`, `login_required`, `upload_failed`, `invalid_request`, `unauthorized`, `queue_full` | `failed` | پیش از ارسال به ChatGPT شکست خورده؛ چیزی تحویل نشده |
| `policy_violation`, `refused`, `image_generation_failed` | `failed` | provider صریحاً گفته تصویری تولید نشده |
| `timeout`, `server_error`, بدون kind | `unknown` | واقعاً نامعلوم؛ هیچ retry خودکاری انجام نمی‌شود |

پیام Job حالا شامل kind، پیام Gateway و `login_blocker` است؛ برای failed عبارت
«safe to re-submit» و برای unknown «check the account manually before retry» ثبت می‌شود.
تست‌های منفی: ۵۰۰ با `page_load_failed` → failed؛ ۵۰۰ بدون kind → unknown.

### ابزار `scripts/resolve_unknown.py`

```bash
python scripts/resolve_unknown.py --password "$ADMIN_PASSWORD"                 # فقط لیست
python scripts/resolve_unknown.py --password ... --match "page_load"           # فیلتر
python scripts/resolve_unknown.py --password ... --resolution not_delivered \
    --match "page_load" --yes                                                  # اعمال
```

- پیش‌فرض read-only است؛ بدون `--yes` هیچ نوشتنی انجام نمی‌شود.
- فقط Jobهای `unknown` که هنوز `resolution` ندارند.
- هر resolve از مسیر endpoint یک‌بارمصرف + audit استفاده می‌کند و upload مرجع را آزاد می‌کند.
- هرگز چیزی را دوباره submit نمی‌کند.

> ۱۵ Job `unknown` فعلی (خطای page-load پیش از ارسال) هنوز به‌صورت دستی تأیید نشده‌اند؛
> خروجی dry-run لیست آن‌ها را نشان می‌دهد. با `--resolution not_delivered --yes` بسته می‌شوند
> و uploadهایشان آزاد می‌شود.

### بررسی پچ‌ها در doctor

`python scripts/doctor.py` حالا:
- وجود `ninja:fast-prompt-insert` را در هر workspace نصب‌شده چک می‌کند (WARN + راهنمای Stop→Start).
- برای workerهای در حال اجرا بررسی می‌کند `RENDER_WAIT_SEC` با تنظیم `render_wait_sec` یکی باشد.
- در اجرای واقعی: `[PASS] worker patches: prompt-insert present in 2 workspace(s); render cap matches`.

### ایزوله‌کردن تست‌ها

تست‌ها پورت‌ها را از `GATEWAY_PORT_BASE` می‌گیرند (conftest → 43000) تا worker زندهٔ پنل
(41000-41999) با تست‌ها تداخل نکند. کل تست‌ها **با پنل و worker زندهٔ در حال اجرا** اجرا شد و
سبز ماند (۱۵۷ passed، ۱ skipped).

### تمرین‌های واقعی این دور

| تمرین | نتیجه |
|---|---|
| backup زنده از دادهٔ واقعی | ۲۸ فایل · ۵۰.۹۹MB · `verify OK` · `PRAGMA integrity_check=ok` · ۴۰ Job در snapshot |
| restore به پوشهٔ خالی | ۴۰ Job و ۲۴ فایل گالری بازیابی شد؛ uploadها (طبق طراحی) در بکاپ نیستند |
| دوام Job در صف با ری‌استارت پنل | Job `queued` با `refs=1` و upload موجود پس از restart؛ سپس cancel و آزادسازی upload |
| اجرای تست‌ها با worker زنده | ۱۵۷ passed، ۱ skipped (بدون تداخل پورت) |

### اعتبارسنجی‌هایی که هنوز انجام نشده (صادقانه)

- **۱۰ اکانت واقعی**: نیاز به ۱۰ اکانت/سرور واقعی؛ ابزار `capacity_report.py` آماده است.
- **۲۴–۴۸ ساعت auto-login**: نیاز به گذشت زمان؛ فقط رفتار کوتاه‌مدت دیده شده.
- **timerهای لینوکس**: فایل‌های systemd آماده‌اند ولی روی این ماشین (Windows) فعال نشده‌اند.

## گیت کیفیت پس از تولید (Quality gate)

با تنظیم **Quality gate** در Operations می‌توان بعد از تولید هر تصویر، همان تصویر را در **همان چت ChatGPT** به مدل داد و نمره گرفت؛ در حالت «fix»، اگر متن/عدد اشتباه باشد یک‌بار در همان چت بازسازی می‌شود.

| حالت | رفتار |
|---|---|
| `0` خاموش | تصویر اول تحویل می‌شود |
| `1` فقط بازبینی | نمره و ایرادها ثبت/نمایش می‌شود؛ تصویر عوض نمی‌شود |
| `2` بازبینی + یک اصلاح | اگر نمرهٔ متن/عدد زیر ۸ یا زیر آستانه باشد، یک‌بار پرامپت اصلاحی در همان conversation_id فرستاده می‌شود؛ فقط اگر نمرهٔ نسخهٔ دوم بهتر بود، نسخهٔ دوم تحویل می‌شود؛ نسخهٔ اول حفظ و URL آن در quality_meta ثبت می‌شود |

**معیار سخت‌گیرانه:** هر حرف/رقم اشتباه (مثل ۱۵ که ۵۱ نوشته شود) حتی با نمرهٔ کلی بالا هم «fix» است؛ نمرهٔ کلی از ضعیف‌ترین نمرهٔ متنی/عددی بالاتر نمی‌رود. متن RTL و نیم‌فاصله هم چک می‌شود.
**محافظ��ها:** حداکثر یک تلاش اصلاح، بدون حلقه؛ خطای بازبینی/اصلاح هرگز Job را fail نمی‌کند؛ رفرنس‌ها برای اصلاح دوباره آپلود نمی‌شوند (چت خودش تصویر را دارد).
**نیاز:** پس از آپدیت، workerها را Stop→Start کن تا وصله‌های gateway (`ninja:quality-gate`) اعمال شوند.
