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

