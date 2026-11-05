# Ninja Image Hub documents

## v0.6 (current)

- [REPORT_V6_FA.md](../REPORT_V6_FA.md) — گزارش سخت‌سازی ۰٫۶٫۰: خرابی‌های v0.5، رفع‌ها، شواهد و موارد تأییدنشده.
- [START_HERE_V6_FA.md](../START_HERE_V6_FA.md) — شروع سریع و جدول عیب‌یابی؛ ترتیب نصب، doctor، دروازه، اجرا روی پورت ۸۰۸۱.
- [FIRST_RUN_FA.md](FIRST_RUN_FA.md) — اولین اجرای عمیق: بررسی doctor، نصب مرورگرها، ورود دستی ChatGPT، اولین تصویر و تأیید بایت اصلی، بک‌آپ و retention.
- [OPERATIONS_V6_FA.md](OPERATIONS_V6_FA.md) — ظرفیت، حالت‌های خرابی، پایش با `/api/health` و کارهای ممنوع.
- [DEPLOY_LINUX_FA.md](DEPLOY_LINUX_FA.md) — استقرار روی سرور لینوکس: Xvfb برای هر worker، systemd، تونل SSH، لاگین بدون مانیتور و استفادهٔ مجدد در اتوماسیون‌های دیگر.
- [ARCHITECTURE_V5_FA.md](ARCHITECTURE_V5_FA.md) — معماری ماژولار؛ طراحی v0.5 که در v0.6 حفظ شده است.
- [FILE_PIPELINE_V5_FA.md](FILE_PIPELINE_V5_FA.md) — مسیر دریافت، ذخیرهٔ اصل، پیش‌نمایش و ZIP.

## v0.5

- [REPORT_V4_FA.md](../REPORT_V4_FA.md)، [REPORT_V5_FA.md](../REPORT_V5_FA.md)، [START_HERE_V5_FA.md](../START_HERE_V5_FA.md) — گزارش و راهنمای نسخهٔ قبل.

## v0.4

- [CONTROL_PLANE_V4_FA.md](CONTROL_PLANE_V4_FA.md) — API/تنظیمات، کنترل هم‌زمانی و حساب‌ها.
- [ANALYTICS_DATA_DICTIONARY_FA.md](ANALYTICS_DATA_DICTIONARY_FA.md) — تعریف آمار اندازه‌گیری‌شده در برابر برآورد؛ عدد تست‌ها برای v0.6 به‌روز شده است.
- `v4-overview-demo.png`، `v4-operations-demo.png`، `v4-analytics-demo.png`، `v4-accounts-demo.png`، `v4-mobile-demo.png` — اسکرین‌شات‌های Chromium با **دادهٔ ساختگی**.

## تاریخی (پیش از v0.5)

- [CONCURRENCY_ARCHITECTURE_FA.md](CONCURRENCY_ARCHITECTURE_FA.md) — **سند تاریخی v0.3** برای هم‌زمانی، صف، بازیابی و ظرفیت. محدوده‌های تنظیمات با کد v0.6 هم‌تراز شده‌اند، اما رفتار و جزئیات پیاده‌سازی v0.3 را توصیف می‌کند؛ برای وضعیت فعلی از `OPERATIONS_V6_FA.md` و `ARCHITECTURE_V5_FA.md` استفاده کنید.
- `v2-accounts-preview.png` / `v2-studio-preview.png` — تصاویر نسخه‌های قبلی؛ کنترل‌های v0.3 را نشان نمی‌دهند.
- `preview-overview-mock-data.png` / `preview-accounts-mock-data.png` — پیش‌نمایش با دادهٔ نمونه. هیچ حساب واقعی ChatGPT برای اسکرین‌شات استفاده نشده است.

**یادداشت v0.3:** تست بصری مرورگری در آن محیط کامل نشد (صفحات محلی و تزریق stylesheet مسدود بود)؛ پیش‌نمایش‌های v0.2 اسکرین‌شات v0.3 نیستند.
