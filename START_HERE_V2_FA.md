# شروع سریع نسخه دوم — Ninja Image Hub

## اجرای محلی روی Windows 11

پیش‌نیازها: Python 3.11+، Node.js 22+، Git و Google Chrome. ZIP را Extract کنید؛ PowerShell را در ریشه پروژه باز کنید.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python scripts/bootstrap.py
python scripts/prepare_gateway.py
python -m uvicorn app:app --host 127.0.0.1 --port 8080
```

مرورگر: `http://127.0.0.1:8080` . رمز Admin را هنگام bootstrap نگهداری کنید. `prepare_gateway.py` با تأیید `INSTALL` سورس پروژه ثالث را از اینترنت دانلود می‌کند. بدون نصب خارجی، فقط بخش مدیریت و UI قابل تست هستند؛ عملیات Login/Generate کار نمی‌کند.

### اولین اکانت

`Accounts → Add account → name → subscription label (optional) → Create → Start → Login → Save Session`

برای لاگین دوم همین مسیر را تکرار کنید. رمز حساب ChatGPT را در فایل `.env` وارد نکنید. در بخش `Edit` می‌توانید برچسب Plus را دستی عوض کنید. وضعیت Plus به‌صورت خودکار بررسی نمی‌شود.

### مدیریت و حذف

- **Edit:** نام، برچسب ایمیل، Autostart و نوع اشتراک دستی.
- **Archive:** توقف Worker بدون پاک‌شدن تصاویر و نشست.
- **Restore:** بازگردانی به فهرست فعال، سپس Start/ Login.
- **Delete forever:** ابتدا Archive؛ سپس Show archived accounts و عبارت تأیید دقیق. تمام داده‌های محلی مربوط به آن اکانت حذف می‌شوند، نه داده‌های سایر اکانت‌ها.

### کیفیت خروجی

`Image Studio → Visual Quality Preference → Best available` صرفاً پرامپت را بهتر راهنمایی می‌کند. این **پارامتر Native `quality=high` مدل ChatGPT نیست**. تصویر دریافتی از Gateway بدون فشرده‌سازی و resize دوباره ذخیره می‌شود. در Gallery رزولوشن، نوع فایل و حجم را می‌بینید؛ Download original نسخه کامل برگشتی Gateway را تحویل می‌دهد.

### ارتقا از نسخه ۰.۱

برنامه قدیمی را متوقف کنید؛ **از `data/` و `.env` بک‌آپ بگیرید** و آنها را همراه نسخه ۰.۲ نگهدارید. `python scripts/bootstrap.py` را روی `.env` موجود دوباره اجرا نکنید مگر اینکه عمداً می‌خواهید Secrets عوض شوند. دیتابیس SQLite نسخه قبلی هنگام شروع به‌صورت افزایشی به‌روزرسانی می‌شود.

### محدودیت مهم

تولید و لاگین واقعی Plus هنوز روی حساب کاربری تست نشده است. اتوماسیون ChatGPT وب برخلاف شرایط OpenAI است و ممکن است باعث تعلیق حساب شود. برای سرویس تولید انبوه به API رسمی مهاجرت کنید. پروژه فعلاً روی `127.0.0.1` اجرا شود تا سرویس‌های روی سرورت مثل Hermes و 9Router دست‌نخورده بمانند.
