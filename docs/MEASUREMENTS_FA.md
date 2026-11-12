# اندازه‌گیری‌ها و ظرفیت واقعی — Ninja Image Hub

این سند فقط **اعداد اندازه‌گیری‌شده** را ثبت می‌کند. هیچ عدد برآوردی، شبیه‌سازی‌شده یا
حاصل از mock در اینجا به‌عنوان ظرفیت provider نوشته نمی‌شود. تست‌های repository از
gateway ساختگی استفاده می‌کنند و «ظرفیت واقعی» نیستند.

## قواعد ثبت

1. هر اندازه‌گیری با دستور، تاریخ UTC، نسخهٔ پنل، تعداد اکانت، پلن و سهمیهٔ مصرف‌شده ثبت می‌شود.
2. خروجی JSON فایل `scripts/capacity_report.py --out` ذخیره می‌شود
   (پیشنهاد: `docs/measurements/capacity-YYYYMMDD-HHMM.json`).
3. تا زمانی که اندازه‌گیری ۱۰ اکانت واقعی انجام نشده، هیچ ادعای ظرفیت ۱۰ اکانت مجاز نیست.

## روش اندازه‌گیری ۱۰ اکانت (روی سرور)

```bash
# 1) همهٔ ۱۰ اکانت باید online/connected باشند (session تازه، پلن مشخص)
python scripts/capacity_report.py --password "$ADMIN_PASSWORD" \
  --submit --yes --accounts 10 --jobs-per-account 1 --n 1 \
  --timeout 3600 --interval 3 \
  --json --out docs/measurements/capacity-$(date -u +%Y%m%d-%H%M).json
```

برای اندازه‌گیری batch (n>1) همان دستور با `--n 4` تکرار می‌شود؛ سهمیهٔ واقعی مصرف می‌شود.

### فیلدهای اجباری گزارش

| فیلد | چرا |
|---|---|
| `wall` | زمان کل تا تسویهٔ همهٔ Jobها |
| `queue_wait_p50_sec` / `p95` | اثر صف و fairness، نه سرعت مدل |
| `generation_p50_sec` / `p95` | زمان واقعی تولید هر Job |
| `images` و `sustained images/h` | خروجی واقعی، نه تعداد درخواست |
| `status counts` | failed/unknown/rate_limited — کیفیت، نه فقط سرعت |
| `effort_below_requested_24h` | آیا High واقعاً اعمال شده |
| `worker RAM` | هزینهٔ هر worker برای برنامه‌ریزی ۱۰ اکانت |
| ثانیه/تاریخ/نسخه/پلن | تکرارپذیری |

## ثبت فعلی (Baseline — نه ۱۰ اکانت)

این عدد **ظرفیت ۱۰ اکانت نیست**؛ فقط عکس فوری محیط توسعه است.

| مورد | مقدار |
|---|---|
| تاریخ | 2026-11-12 (UTC) |
| نسخه | 0.15.0 |
| اکانت‌ها | ۲ (Main: online/connected، دوم: stopped/unknown) |
| workerهای در حال اجرا | ۱ |
| RAM worker | ~۱۵۳۴.۷ MB برای Main |
| limits | `max_running_accounts=2` · `global_max_inflight=4` · `max_batch_images=4` · tabs=3 |
| صف | ۰ queued · ۱ running (در لحظهٔ snapshot) |
| unresolved unknown | ۱۷ (از تلاش‌های قبلی، نیازمند resolve) |
| disk free | ۶۵۳۰ MB · disk_low=false |
| effort downgrade 24h | ۰ · mixed ۰ |
| JSON | `docs/measurements/capacity-20261112-local.json` |

## Soak 48 ساعته

```bash
python scripts/soak.py --password "$ADMIN_PASSWORD" --hours 48 --interval 900 \
  --out logs/soak-48h.csv --summary logs/soak-48h.json \
  --submit --yes --jobs 3        # اختیاری: حداکثر ۳ Job واقعی
```

- پیش‌فرض بدون Job واقعی است (فقط health/session/queue) تا سهمیه مصرف نشود.
- `login_issue` فقط برای workerهای **online** که session آن‌ها connected نیست ثبت می‌شود؛
  اکانت stopped هشدار کاذب نمی‌سازد.
- معیار پذیرش: ۴۸ ساعت بدون login دستی، یا مستند دقیق زمان انقضا و بازیابی.

## وضعیت واقعی فعلی (صادقانه)

| ادعا | وضعیت |
|---|---|
| ۱۰ اکانت / ۱۰ تولید هم‌زمان | **اندازه‌گیری نشده** — نیازمند ۱۰ اکانت واقعی و سهمیه |
| ۲۴–۴۸h auto-login | **اندازه‌گیری نشده** — `scripts/soak.py` آماده است |
| ظرفیت sustained images/h | **ندارد** — فقط بعد از اجرای بالا ثبت شود |
| رفتار High effort در پلن‌های مختلف | فقط از echo گیت‌وی تست شده؛ نه همهٔ پلن‌ها |
