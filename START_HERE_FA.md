# شروع سریع Ninja Image Hub

این پروژه یک **پنل وب انگلیسی واقعی** با بک‌اند Python/FastAPI است. برای شروع لازم نیست به سرور فعلی یا Hermes و 9Router دست بزنید؛ روی Windows 11 نصب و تست محلی را شروع کنید.

## مرحله ۱: پیش‌نیاز

Python 3.11 یا جدیدتر، Git، Node.js 22+ و Google Chrome را نصب کنید. از داخل پوشه پروژه PowerShell باز کنید.

## مرحله ۲: دستورات نصب

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python scripts/bootstrap.py
python scripts/prepare_gateway.py
python -m uvicorn app:app --host 127.0.0.1 --port 8080
```

در مرحله prepare وقتی از شما خواسته شد `INSTALL` را تایپ کنید؛ این مرحله **سورس خارجی Gateway و وابستگی‌های آن را از GitHub دانلود می‌کند**. اینترنت و مجوز دسترسی نصب لازم است. پسورد Admin و Agent API Key هنگام اجرای bootstrap یک‌بار نمایش داده می‌شوند؛ در جای امن نگهداری کنید.

اگر فعال‌کردن محیط مجازی در PowerShell محدود شد، به‌جای `python` از `.\.venv\Scripts\python.exe` استفاده کنید.

## مرحله ۳: پنل

آدرس: **http://127.0.0.1:8080**

با رمز Admin خودتان وارد شوید. سپس:

1. **Accounts → Add account**؛ یک اسم برای اکانت ثبت کنید.
2. **Start** (اگر قبلاً Auto Start نشده)، سپس **Login**.
3. در پنجره Login، داخل مرورگر کنترل‌شده با اکانت متعلق به خودتان وارد ChatGPT شوید، در پایان **Save Session** را بزنید.
4. برای هر اکانت دیگر همین مرحله را تکرار کنید؛ نشست هر اکانت جدا است.
5. **Image Studio → Select account → Prompt → Generate** و خروجی را در **Jobs / Gallery** ببینید.

در Windows برای ورود خودکار به ChatGPT از واردکردن رمز در `.env` استفاده نشده است. اعتبار نشست ممکن است منقضی شود؛ در آن صورت Login باید تکرار شود.

## مرحله ۴: اتصال ایجنت

مسیرها از قبل وجود دارند و نیاز به اجرای DeepSeek داخل این پروژه نیست:

- `POST /v1/images/generations` (ایجاد Job و دریافت شناسه)
- `GET /v1/jobs/{id}` (دریافت وضعیت)
- `GET /v1/images/{id}` (دریافت فایل نتیجه)

در هر درخواست هدر `Authorization: Bearer <AGENT_API_KEY>` لازم است. شکل دقیق JSON در `README.md` آمده است.

## مرحله ۵: تفسیر نتیجه تست‌ها

تست‌های کدنویسی و ارتباط پنل تا یک Gateway شبیه‌سازی‌شده موفق بوده‌اند. **لاگین واقعی و تولید عکس با اکانت ChatGPT در محیط ساخت تست نشده است.** خود سرویس اتوماسیون مرورگر غیررسمی است و ممکن است خلاف شرایط استفاده OpenAI باشد. برای استفاده تجاری پایدار و رعایت شرایط سرویس، بهتر است از API رسمی تصاویر استفاده کنید.

گزارش کامل فنی و ریسک‌ها در `REPORT_FA.md` و راهنمای نصب فنی در `README.md` موجود است.
