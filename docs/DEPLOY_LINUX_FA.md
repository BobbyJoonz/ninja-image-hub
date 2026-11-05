# استقرار روی سرور لینوکس — راهنمای کامل

این سند به سه سؤال جواب می‌دهد: آیا مرورگر باید باز بماند؟ روی سرور بدون دسکتاپ چطور کار می‌کند؟ و چطور اکانت اضافه/لاگین کنیم؟

---

## ۱. کوتاه: «مرورگر باز است» یعنی چه؟

gateway عمداً **headless نیست**. در `hub/workers.py` متغیر `HEADLESS=false` به worker داده می‌شود، چون یک مرورگر headless برای ChatGPT نشانهٔ واضح ربات است و معمولاً به Cloudflare challenge می‌خورد. پس:

- **روی ویندوز/مک**: هر اکانتِ روشن، یک پنجرهٔ Chrome واقعی دارد (همین الآن یک پنجره باز است). بستن آن پنجره = مرگ worker؛ supervisor تا ۸ بار برش می‌گرداند.
- **روی سرور لینوکس**: پنجره‌ای روی میز نیست، اما همان مرورگر واقعی روی یک **نمایشگر مجازی (Xvfb)** رندر می‌شود. هر worker خودش یک Xvfb جدا می‌گیرد (`xvfb-run -a -s "-screen 0 1440x900x24"`)، بنابراین:
  - پردازش و رم مثل یک Chrome واقعی است (۰.۸ تا ۱.۳ گیگابایت به‌ازای هر اکانت).
  - رندر و اسکرین‌شات کار می‌کند، کلیک و تایپ هم (از طریق پنل).
  - هیچ کاربری لازم نیست روی کنسول سرور باشد.
  - اکانت‌ها پنجرهٔ مشترک ندارند؛ هر اکانت context مرورگر خودش را دارد.

> اگر `DISPLAY` واقعی (VNC/X11) موجود باشد، Hub از آن استفاده می‌کند و Xvfb نمی‌سازد.

---

## ۲. نصب با یک اسکریپت

پیش‌نیاز: Ubuntu/Debian با root، دسترسی اینترنت (برای npm و Chrome).

```bash
# روی سرور، داخل همان پوشهٔ پروژه
sudo bash deploy/install_linux.sh
```

این اسکریپت به ترتیب انجام می‌دهد:

1. پکیج‌های سیستمی: `xvfb`، فونت‌ها (`fonts-liberation`, `fonts-noto-color-emoji`) و کتابخانه‌های Chrome (`libnss3`, `libgbm1`, …).
2. Node.js ۲۲ (اگر قدیمی‌تر بود از NodeSource نصب می‌کند).
3. Google Chrome (از `dl.google.com`؛ اگر شبکه بست، پیام می‌دهد).
4. کاربر سیستمی `ninja` و کپی سورس به `/opt/ninja-image-hub` (بدون `.env`، `data/`، `vendor/`).
5. venv پایتون + نصب `requirements.txt`.
6. `python scripts/bootstrap.py` (فقط اگر `.env` نبود؛ رمز ادمین یک‌بار چاپ می‌شود — ذخیره کن).
7. نصب و فعال‌سازی سرویس `ninja-image-hub.service`.

ارتقا در آینده:

```bash
sudo bash deploy/install_linux.sh --upgrade
```

`.env`، `data/` و `vendor/` دست‌نخورده می‌مانند (سورس فقط با `rsync --delete` به‌روز می‌شود و این سه مورد exclude هستند).

### نصب دستی (اگر اسکریپت را نمی‌خواهی)

```bash
sudo apt install -y python3 python3-venv xvfb fonts-liberation libnss3 libgbm1 \
  libatk1.0-0 libatk-bridge2.0-0 libcups2 libasound2 libxkbcommon0 libxcomposite1 \
  libxdamage1 libxfixes3 libxrandr2 libpango-1.0-0 libcairo2
# Node 22 + Chrome را نصب کن، بعد:
cd /opt/ninja-image-hub
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/bootstrap.py
.venv/bin/python scripts/doctor.py          # باید بدون FAIL باشد
.venv/bin/python scripts/prepare_gateway.py --yes
sudo cp deploy/ninja-image-hub.service /etc/systemd/system/
sudo systemctl enable --now ninja-image-hub
```

---

## ۳. دسترسی امن به پنل

پنل روی loopback (`127.0.0.1:8081`) گوش می‌دهد. **هرگز پورت ۸۰۸۱ را مستقیم روی اینترنت باز نکن**؛ اگر باز شود، هر کسی می‌تواند با رمز ادمین وارد شود و از صفحهٔ لاگین روی اکانت ChatGPT تو تایپ کند.

روش پیشنهادی — تونل SSH از لپ‌تاپ:

```bash
ssh -N -L 8081:127.0.0.1:8081 user@server
# مرورگر: http://127.0.0.1:8081
```

اگر واقعاً باید از راه دور و بدون SSH استفاده شود: Caddy/Nginx با HTTPS + Basic Auth + IP allowlist، و حتماً `COOKIE_SECURE=true` در `.env` (پس از فعال‌سازی HTTPS).

---

## ۴. افزودن اکانت و لاگین بدون مانیتور

نیازی به دیدن دسکتاپ سرور نیست. Hub خودش تصویر مرورگر را از gateway می‌گیرد و کلیک/تایپ را به آن می‌فرستد:

1. پنل → **Accounts → Add account** → نام بده → **Start**.
2. چند ثانیه صبر کن تا وضعیت `online` شود (اولین بار Chrome و Xvfb بالا می‌آید).
3. **Accounts → Login** (یا `http://127.0.0.1:8081/accounts/<ACCOUNT_ID>/login`).
4. در همان صفحه دکمهٔ **Open ChatGPT** را بزن؛ تصویر زندهٔ مرورگرِ سرور را می‌بینی.
5. با **Type / Tab / Enter / کلیک روی تصویر** وارد شو (رمز و 2FA از همان‌جا؛ پنل چیزی ذخیره نمی‌کند).
6. بعد از ورود، **Save Session** را بزن. این کار کوکی‌ها را در
   `data/accounts/<id>/gateway/auth/session.json` ذخیره می‌کند؛ پس از ری‌استارت سرور هم سشن برمی‌گردد.
7. صفحه را ببند و مستقیم یک Job بفرست. اگر برچسب سشن `unverified` مانده نگران نباش؛ اولین درخواست واقعی آن را تأیید می‌کند. اگر بعد از ۱–۲ دقیقه باز هم بود: Stop/Start همان اکانت (سشن ذخیره‌شده بازیابی می‌شود).

### «Open ChatGPT» را همیشه اول بزن

gateway یک «login session» فعال دارد؛ اگر آن را باز نکنی، اسکرین‌شات با **۴۰۹ «login session is not active»** رد می‌شود و صفحه پیام «Worker unavailable or login session not opened» می‌دهد. Save Session پایانش می‌دهد؛ پس دوباره برای کار با صفحه، Open ChatGPT را بزن.

### گزینه‌های پیشرفته (اختیاری)

| سناریو | تنظیم |
|---|---|
| می‌خواهی با VNC خودت ببینی | `apt install x11vnc` و `Xvfb :99 -screen 0 1440x900x24 & export DISPLAY=:99`؛ Hub دیگر Xvfb خودش را نمی‌سازد |
| Chrome را بیرون از Hub اجرا می‌کنی | `BROWSER_MODE=cdp` + `CDP_URL=http://127.0.0.1:9222` در env همان worker (پیشرفته) |
| سرور فقط has headless | کار نمی‌کند؛ حتماً Xvfb لازم است (headless = ریسک بالای بلاک) |

---

## ۵. عملیات روزمره (systemd)

```bash
sudo systemctl status ninja-image-hub          # وضعیت
sudo systemctl restart ninja-image-hub         # پس از تغییر .env یا ارتقا
journalctl -u ninja-image-hub -f               # لاگ پنل
tail -f /opt/ninja-image-hub/data/accounts/*/worker.log   # لاگ هر مرورگر
curl -s http://127.0.0.1:8081/api/health        # پایش سریع
```

- **فقط یک فرایند Uvicorn**: در unit نوشته شده `--workers 1`؛ آن را عوض نکن. صف، supervisor و قفل‌های dispatcher در همین فرایند هستند.
- `.env` را عوض کردی → `systemctl restart`. تنظیمات داشبورد (Settings) بدون ری‌استارت زنده‌اند.
- بکاپ: `.env` + کل `data/` (SQLite در حالت WAL است، پس `hub.db` و `hub.db-wal`/`-shm` با هم و در حالت توقف سرویس کپی شوند، یا از `sqlite3 .backup` استفاده کن):

```bash
sudo systemctl stop ninja-image-hub
sudo tar czf ~/ninja-backup-$(date +%F).tar.gz -C /opt/ninja-image-hub .env data
sudo systemctl start ninja-image-hub
```

- یک Job در حال اجرا را کرش نکن: اگر `unknown` شد، **دوباره نفرست**؛ اول نتیجه را دستی بررسی کن.

---

## ۶. ظرفیت و رم

| مورد | مصرف |
|---|---|
| پنل + پایتون (بدون worker) | ~۱۵۰–۲۵۰ MB |
| هر اکانت worker (Node + Chrome + Xvfb) | **۰.۸–۱.۳ GB** (بی‌کار)، بیشتر هنگام رندر |
| حالت پیش‌فرض v0.6 | **workers روی تقاضا**: بی‌کار بعد از ۲۰ دقیقه می‌خوابد، پس سرور در حالت بی‌کاری فقط ~۲۵۰MB مصرف می‌کند |
| `idle_worker_shutdown_minutes` | ۰ = همیشه باز؛ ۵–۳۰ = پیشنهادی برای سرور |
| `max_running_accounts` پیش‌فرض | ۲ (از Settings قابل تغییر تا ۱۶؛ ولی رم را حساب کن) |
| هر تصویر PNG | ۲–۵ MB + پیش‌نمایش WebP کوچک |
| بین دو Job هر اکانت | حداقل ۶۰ ثانیه (`MIN_JOB_GAP_SEC` در gateway) |

برای یک اکانت ۲GB RAM کافی است (چون در حالت بی‌کاری مرورگر خاموش است)؛ برای پردازش هم‌زمان دو اکانت ۴GB و بالاتر منطقی است. `MemoryMax=6G` در unit نمونه است؛ مطابق سرورت تنظیمش کن.

---

## ۷. عیب‌یابی

| نشانه | علت و راه‌حل |
|---|---|
| `browser display: no DISPLAY and no xvfb-run` | `sudo apt install -y xvfb`، بعد ری‌استارت سرویس |
| worker `online` نمی‌شود | `tail data/accounts/*/worker.log`؛ اگر «browser not found» بود Google Chrome نصب نیست؛ اگر خطای کتابخانه بود `doctor.py` نام پکیج را می‌گوید |
| صفحهٔ لاگین: «Worker unavailable or login session not opened» | دکمهٔ **Open ChatGPT** را بزن (login session را فعال می‌کند) |
| `Save Session` کار نمی‌کند | ابتدا Open ChatGPT، سپس Save؛ لاگ worker باید `POST /login/save < 200` نشان دهد |
| سشن `unverified` می‌ماند | تا ۵ دقیقه یا تا اولین Job صبر کن؛ یا Stop/Start اکانت |
| Cloudflare / Turnstile | از شبکهٔ IP سرور رد می‌شود؛ gateway تلاش مجدد دارد؛ در صورت تکرار، IP/egress یا زمان دیگری را امتحان کن |
| `database is locked` | نباید رخ دهد (WAL فعال است)؛ اگر شد، نسخهٔ دومی از پنل در حال اجراست |
| رم تمام می‌شود | تعداد اکانت روشن را کم کن (Stop) یا `max_running_accounts` را پایین بیاور |

---

## ۸. استفادهٔ مجدد در اتوماسیون‌های دیگر (چه بخشی قابل جدا شدن است؟)

لایه‌بندی پروژه عمداً طوری است که فقط یک «لبه» مخصوص ChatGPT باشد:

### عمومی و قابل استفادهٔ مجدد (بدون تغییر)

| لایه | فایل | کارکرد |
|---|---|---|
| صف durable + idempotency | `hub/jobs.py` | Job با کلید یکتا، ۴۰۹ برای payload متفاوت، سقف صف |
| زمان‌بند عادلانه | `hub/scheduler.py` | سقف global/per-account، round-robin، cooldown، requeue، supervisor کرش |
| چرخهٔ حساب/worker | `hub/accounts.py`, `hub/workers.py` | پورت اختصاصی، env allowlist، Xvfb، شروع/توقف امن |
| خط لولهٔ فایل | `hub/media.py`, `hub/storage.py`, `hub/delivery.py` | دریافت chunked با سقف، fsync، انتشار اتمیک بدون overwrite، thumbnail، ZIP، Range |
| کنترل پلن + audit | `hub/control.py`, `hub/system.py` | تنظیمات زنده با CSRF، شمارنده‌ها، `/api/health` |
| احراز هویت | `hub/security.py`, `hub/limits.py` | session/Bearer، سقف ۶۴KiB |

### مخصوص این provider (فقط این‌ها را عوض کن)

| نقطه | فایل | قرارداد |
|---|---|---|
| فراخوانی تولید | `hub/gateway.request_generation(url, secret, payload, n)` | باید شیئی با `status_code`, `headers`, `data` برگرداند؛ `data = {"data": [{b64_json|url}, …]}` |
| آدرس‌های مجاز تصویر | `hub/media.approved_gateway_url` | همان قاعدٔ loopback+پورت؛ برای API ابری این تابع را عوض کن |
| env و کامند worker | `hub/workers.worker_env` / `worker_command` | هر فرایندی که `/health`, `/metrics`, `/v1/images/generations` را با Bearer بدهد |
| صفحهٔ لاگین ریموت | `hub/login.py` | فقط اگر provider سشن مرورگری دارد |

### دستور پخت برای اتوماسیون دیگر (مثلاً API رسمی تصویر)

1. یک adapter بنویس که همان شکل `SimpleNamespace(status_code, headers, data)` را برگرداند و `data` را با `b64_json` یا `url` پر کند.
2. در `hub/gateway.py` تابع `request_generation` را به آن delegate کن (یا یک کلید `PROVIDER` در `control_settings` اضافه کن).
3. اگر ابری است، `media.approved_gateway_url` را با allowlist دامنهٔ رسمی عوض کن **و هیچ‌وقت** allowlist را به `*` تبدیل نکن.
4. «اکانت» را به معنی کلید API بگیر: `api_secret` همان کلید، `parallel_limit` همان سهمیه است. بقیهٔ صف/idempotency/گالری بدون تغییر کار می‌کند.
5. تست‌ها را با fake adapter بگذار (الگوی `tests/test_app.py::test_full_generation_path_with_isolated_mock_gateway`).

یعنی: **۹۰٪ این پروژه (صف، fairness، پایداری، فایل، پنل) مستقل از ChatGPT است**؛ تنها ~۱۰٪ (gateway contract + login) مخصوص این روش است.

---

## ۹. محدودیت‌های واقعی

- اتوماسیون UI چت‌GPT خلاف شرایط استفادهٔ OpenAI است و ریسک بن دارد؛ اکانت اختصاصی استفاده کن.
- این یک سرویس SLAدار نیست: Cloudflare، تغییر UI، 2FA و محدودیت‌های حساب همه بیرون از کنترل این نرم‌افزارند.
- PostgreSQL و چند replica پشتیبانی نمی‌شود؛ یک فرایند، یک دیتابیس.
- روی سرور مشترک (multi-tenant) این سرویس را اجرا نکن: هر اکانت یک سشن ChatGPT واقعی دارد.
