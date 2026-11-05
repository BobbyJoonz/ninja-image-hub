# شروع و ارتقای Ninja Image Hub v0.4

## مسیر سریع در Windows 11
ابتدا نسخه قبلی را متوقف کن. قبل از استخراج نسخه جدید، از `.env` و **کل پوشه `data/`** و دیتابیس PostgreSQL (در صورت استفاده) نسخه پشتیبان بگیر. برنامه فعلی **نباید همزمان با نسخه قدیمی روی همان دیتابیس** اجرا شود.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts/bootstrap.py
python scripts/prepare_gateway.py
python -m uvicorn app:app --host 127.0.0.1 --port 8080 --workers 1
```

- `bootstrap.py` برای راه‌اندازی تازه است؛ اگر از نسخه قبلی ارتقا می‌دهی، فایل `.env` موجود و secrets قبلی را حفظ کن و بدون دلیل دوباره bootstrap نکن.
- `prepare_gateway.py` نیازمند Node.js، Git و Chrome/Chromium است. Gateway بیرونی در ZIP قرار ندارد.
- آدرس پنل `http://127.0.0.1:8080`؛ سپس Login → Accounts → Add Account → Start → Login.
- داخل `Operations` هدف تولید، سقف هم‌زمانی، تعداد تب‌ها، ظرفیت صف، تعداد تصویر در Job، سقف Worker و Cooldown را ببین یا ویرایش کن.
- تغییر `Gateway tabs` و `Max images` سمت موتور مرورگر تا Stop/Start کردن Worker **کامل اعمال نمی‌شود**؛ Job در حال اجرا را قطع نکن.
- در `Performance` دوره 5H/24H/7D/30D را عوض کن و خروجی واقعی و وضعیت خطاها را ببین.
- در `Accounts` اکانت را بدون خروج از سیستم Pause queue یا Resume queue کن؛ آرشیو و حذف دائمی مسیر جدا دارند.

## تست بی‌خطر قبل از اتصال
```powershell
python -m pytest -q
node --check frontend/app.js
```
پیش‌نمایش‌های `docs/v4-*-demo.png` **با داده ساختگی** ساخته شده‌اند، نه اکانت واقعی.

## مسیر لینوکس و سرور
همان `python -m uvicorn app:app --host 127.0.0.1 --port 8080 --workers 1` را اجرا کن. برای اینترنت عمومی از reverse proxy، TLS، فایروال و بک‌آپ استفاده کن؛ Gateway روی localhost بماند. این سرویس را کنار Hermes/9Router بدون جداسازی پورت، داده و کانتینر اجرا نکن. هیچ فایل یا تنظیم مربوط به آنها در این ZIP تغییر نمی‌کند.

## محدودیت‌های مهم
- Plus: اتوماسیون رابط ChatGPT و استخراج برنامه‌نویسی‌شده خروجی در شرایط رسمی OpenAI مجاز نیست؛ خطر محدودشدن اکانت وجود دارد.
- تنظیم `Best/Balanced/Draft` برای حالت مرورگر همان پارامتر کیفیت `high/medium/low` در OpenAI API نیست؛ فقط راهنمای متن پرامپت است.
- هیچ اتصال واقعی اکانت Plus در این محیط تست نشده؛ استفاده عملیاتی ۵۰۰ تصویر در ۵ ساعت تضمین نشده.
- اعداد ظرفیت پنل سیاست نرم‌افزارند، نه ضمانت ارائه‌دهنده. برای محصول پایدار از ارائه‌دهنده مجاز استفاده کن.
