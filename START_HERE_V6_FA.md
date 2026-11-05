# شروع سریع — Ninja Image Hub v0.6

این مسیر برای نصب تازه روی ویندوز است. اگر از v0.5 ارتقا می‌دهید، مستقیم به بخش «ارتقا» بروید.

> **صداقت دربارهٔ پیش‌نمایش:** نسخهٔ Site/Preview با دادهٔ نمونه است و به ارائه‌دهنده وصل نمی‌شود. برای حساب و تصویر واقعی باید پنل Python را روی `127.0.0.1` اجرا کنید. هیچ ادعایی دربارهٔ ورود یا تولید واقعی ChatGPT Plus در این نسخه تأیید نشده است.

## ۰. پیش‌نیازها

| مورد | حداقل | بررسی |
|---|---|---|
| Python | 3.11+ (تست‌شده روی 3.12) | `python --version` |
| Node.js | 22+ | `node --version` |
| npm و Git | روی PATH | `npm --version` / `git --version` |
| Google Chrome | نصب‌شده (patchright از Chrome واقعی استفاده می‌کند) | `python scripts/doctor.py` |

## ۱. نصب

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## ۲. ساخت `.env`

```powershell
python scripts/bootstrap.py
```

رمز ادمین و کلید ایجنت فقط همین یک بار چاپ می‌شوند؛ آن‌ها را در مدیر رمز نگه دارید. این فرمان فایل `.env` موجود را بازنویسی نمی‌کند.

## ۳. doctor (پیش از اجرا)

```powershell
python scripts/doctor.py
```

- خروج ۰ = آمادهٔ شروع؛ خروج ۱ = حداقل یک FAIL.
- برای خروجی ماشین‌خوان: `python scripts/doctor.py --json`.
- هر FAIL خط `->` دارد که فرمان رفع را نشان می‌دهد. WARN معمولاً مانع شروع نیست، ولی باید فهمیده شود.

## ۴. نصب دروازهٔ مرورگر و مرورگرهای آن

```powershell
python scripts/prepare_gateway.py
```

اسکریپت تأیید تعاملی می‌خواهد؛ برای اجرای خودکار `--yes` بدهید. نصب کامل یعنی `npm ci` **و** نصب هر دو مرورگر:

- `npx playwright install chromium`
- `npx patchright install chrome`

اگر فقط وابستگی Node را می‌خواهید (و مرورگرها را دستی نصب می‌کنید) از `--skip-browsers` استفاده کنید. نسخهٔ ثبت‌شده در `vendor/PINNED_COMMIT.txt` نگه داشته می‌شود.

**اگر شبکه محدود است:** وقتی `github.com` بسته باشد، اسکریپت به‌طور خودکار از tarball سایت `codeload.github.com` نصب می‌کند. وقتی دانلود مرورگر Playwright هم مسدود باشد، فقط هشدار می‌دهد، چون موتور پیش‌فرض gateway (patchright) از **Google Chrome نصب‌شده** روی سیستم استفاده می‌کند؛ `doctor.py` می‌گوید کدام مسیر مرورگر فعال است.

**اجرای یک‌کلیکی ویندوز:** روی `start_windows.bat` دوبار کلیک کنید؛ بار اول `.env` می‌سازد و بعد پنل را روی `http://127.0.0.1:8081` بالا می‌آورد.

## ۵. اجرای پنل

```powershell
python -m uvicorn app:app --host 127.0.0.1 --port 8081 --workers 1
```

- **پورت ۸۰۸۰ را پیش‌فرض نگیرید:** روی این ماشین Adobe Connect پورت `127.0.0.1:8080` را اشغال کرده و اجرای `--port 8080` خطای «address already in use» می‌دهد. `8081` مثال امن است.
- اگر پورت دیگری هم اشغال بود، `doctor.py` یک پورت آزاد پیشنهاد می‌دهد.
- فقط **یک** فرایند Uvicorn؛ `--workers 1` و بدون `--reload` وقتی Job فعال است.

## ۶. ورود

مرورگر را باز کنید: `http://127.0.0.1:8081` و با رمز ادمینِ bootstrap وارد شوید.

## ۷. افزودن حساب

در بخش **Accounts** → **Add Account**. پورت worker به‌صورت خودکار از بازهٔ ۴۱۰۰۰ به بالا انتخاب می‌شود. پس از ساخت، حساب را **Start** کنید و منتظر بمانید تا وضعیت `online` و سپس نشست `connected` شود.

## ۸. ورود دستی ChatGPT

برای هر حساب، صفحهٔ ورود تعبیه‌شده را باز کنید:

```text
http://127.0.0.1:8081/accounts/<ACCOUNT_ID>/login
```

داخل همان صفحه، ورود را **دستی** انجام دهید (ایمیل/رمز/کد دومرحله‌ای خودتان)، سپس **Save** را بزنید. نشست در پوشهٔ همان حساب زیر `DATA_DIR` نگه داشته می‌شود. Hub کوکی را لاگ یا صادر نمی‌کند و MFA/CAPTCHA را دور نمی‌زند.

## ۹. اولین Job

در **Image Studio**: پرامپت، تعداد تصویر (`n`)، نسبت تصویر و ترجیح `Best/Balanced/Draft` را وارد کنید و ارسال کنید. `Best` فقط راهنمای متن پرامپت است، نه کیفیت رسمی API.

- وضعیت‌ها را در **Jobs/Gallery** ببینید: `queued` → `running` → `completed` / `partial` / `failed` / `rate_limited` / `unknown`.
- اگر پاسخ ثبت سفارش قطع شد، همان فرم را دوباره بفرستید؛ کلید idempotency در `sessionStorage` همان تب حفظ می‌شود و Job تکراری ساخته نمی‌شود. اگر تب را بستید، اول Jobs را بررسی کنید.
- `unknown` یعنی ممکن است خروجی upstream ساخته شده باشد؛ **دوباره ارسال نکنید** و ابتدا دستی بررسی کنید.

## ۱۰. تصاویر کجا ذخیره می‌شوند؟

اصل فایل‌ها بدون re-encode در این مسیر ذخیره می‌شوند:

```text
<DATA_DIR>\gallery\<job_id>-<position>.png
```

پیش‌فرض `DATA_DIR` پوشهٔ `data/` پروژه است. ردیف هر تصویر در جدول `job_images` دیتابیس `data\hub.db` ثبت می‌شود و از **Gallery** می‌توانید اصل یا ZIP همان Job را بگیرید. پیش‌نمایش‌های WebP فقط مشتق‌شده‌اند و در `data\previews` قرار می‌گیرند؛ آن‌ها جای اصل فایل را نمی‌گیرند.

## workers روی تقاضا (پیش‌فرض)

مرورگر فقط هنگام کار باز است: پس از ۲۰ دقیقه بی‌کاری خودش بسته می‌شود و با اولین Job (یا باز کردن صفحهٔ Login) خودکار بیدار می‌شود. برای تغییر: Operations → «Idle browser shutdown (min)»؛ مقدار ۰ یعنی همیشه باز. حساب را دستی Stop نکن، وگرنه Jobهایش در صف می‌مانند.

## ارتقا از v0.5

۱. Pause کردن ارسال، انتظار برای Jobهای فعال، سپس توقف فرایند قدیمی.
۲. بک‌آپ از `.env` و کل `data/` (به‌دلیل WAL فقط `hub.db` کافی نیست).
۳. جایگزینی سورس با حفظ `.env` و `data/`؛ اجرای دوبارهٔ bootstrap لازم نیست.
۴. `pip install -r requirements.txt` → `python scripts/doctor.py` → اجرای تک‌فرایندی روی `8081`.
۵. مهاجرت SQL افزایشی است؛ Jobهای `running` باقی‌مانده پس از قطع ناگهانی `unknown` می‌شوند.

## اگر این خطا را دیدی

| نشانه | علت محتمل | کار |
|---|---|---|
| `[Errno 10048] address already in use` / «address already in use» | پورت اشغال است؛ روی این ماشین ۸۰۸۰ در اختیار Adobe Connect است | `--port 8081` (یا پورت پیشنهادی doctor)؛ مطمئن شوید فقط یک Uvicorn اجراست |
| صفحه روی فرم ورود می‌ماند و هیچ درخواستی به `/api/*` نمی‌رود | `.js` با MIME غیر JS سرو می‌شود (نگاشت رجیستری ویندوز) | پنل خودش MIME را pin می‌کند؛ پنل را با Uvicorn اجرا کنید، نه `python -m http.server`. برای preview از `python scripts/serve_preview.py --port 8090` استفاده کنید |
| doctor: `google chrome: not found` یا worker با خطای مرورگر بالا نمی‌آید | Chrome نصب نیست یا patchright مرورگر را نصب نکرده | Google Chrome را نصب کنید و در `vendor/gpt-web-gateway` دستور `npx patchright install chrome` را اجرا کنید |
| doctor: `gateway: not installed` | `vendor/gpt-web-gateway/src/server.js` وجود ندارد | `python scripts/prepare_gateway.py` (با اینترنت و Git/Node) |
| وضعیت نشست `unverified` یا `unknown`؛ ورود لازم است | نشست ChatGPT منقضی شده یا ورود کامل نشده | صفحهٔ `/accounts/<id>/login` را باز کنید و دستی دوباره وارد شوید و Save بزنید |
| `rate_limited` و `cooldown_until` در حساب | پاسخ 429 از ارائه‌دهنده | منتظر پایان cooldown بمانید؛ حساب عوض نکنید و محدودیت را دور نزنید |
| Job در `unknown` ماند | نتیجهٔ ارسال upstream نامعلوم است | دستی بررسی کنید؛ دوباره ارسال نکنید. Hub هیچ replay خودکاری ندارد |
| doctor: `env file: .env is missing` | bootstrap اجرا نشده یا `.env` جابه‌جا شده | `python scripts/bootstrap.py`؛ در نصب موجود، `.env` را از بک‌آپ برگردانید |
| doctor: `python packages: missing ...` | وابستگی‌ها نصب نیستند | `pip install -r requirements.txt` داخل venv فعال |

## روی سرور لینوکس

```bash
sudo bash deploy/install_linux.sh          # پکیج‌ها، Node 22، Chrome، Xvfb، venv، .env، systemd
sudo -u ninja -i && cd /opt/ninja-image-hub
.venv/bin/python scripts/doctor.py
.venv/bin/python scripts/prepare_gateway.py --yes
sudo systemctl restart ninja-image-hub
```

پنل روی loopback می‌ماند؛ از لپ‌تاپ با `ssh -N -L 8081:127.0.0.1:8081 user@server` وصل شو. برای **لاگین ChatGPT روی سرور هیچ مانیتوری لازم نیست**: Accounts → Login تصویر مرورگر سرور را پخش می‌کند و کلیک/تایپ را می‌فرستد؛ بعد از ورود **Save Session** بزن. مرورگر headless نیست، روی نمایشگر مجازی Xvfb (یکی برای هر اکانت) اجرا می‌شود.

جزئیات کامل: [docs/DEPLOY_LINUX_FA.md](docs/DEPLOY_LINUX_FA.md)

برای جزئیات بیشتر: [REPORT_V6_FA.md](REPORT_V6_FA.md) · [docs/FIRST_RUN_FA.md](docs/FIRST_RUN_FA.md) · [docs/OPERATIONS_V6_FA.md](docs/OPERATIONS_V6_FA.md) · [docs/README.md](docs/README.md)
