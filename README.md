<div align="center">

# 🧾 Sale Bot

**Telegram sales accounting for Persian craft & event sellers**

Turn your sales channel into a ledger. No spreadsheets, no manual tallying.

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![python-telegram-bot](https://img.shields.io/badge/python--telegram--bot-21.9-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](https://python-telegram-bot.org)
[![Tests](https://img.shields.io/badge/tests-57_passing-2EA043?style=for-the-badge)](#-testing)
[![License](https://img.shields.io/badge/license-MIT-blue?style=for-the-badge)](LICENSE)

🇬🇧 **English** | [🇮🇷 فارسی](#-فارسی)

</div>

---

## The problem

You sell handmade goods at events. Every sale goes into a Telegram channel as a
quick post — item name, price, event, date. By the end of the month there are two
hundred posts and someone has to scroll through all of them with a calculator to
answer a simple question: *how much did we make between the 27th and the 29th?*

## What this does

Sale Bot reads those posts as they arrive, pulls out the amount and the Jalali
date, and keeps a running ledger in SQLite. Then you ask it questions.

```
سنجاق سینه جا عینکی          ← the bot reads this post…
۷۰۰t
ایونت آلما
 ۶ شهریور۱۴۰۵
```

```
/sum ۵ شهریور تا ۷ شهریور     ← …and answers this
```

```
جمع فروش
از ۵ شهریور ۱۴۰۵ تا ۷ شهریور ۱۴۰۵

مبلغ بازه: ۲,۲۵۰ t
تعداد فروش: ۳
───────────────
جمع کل دفتر: ۳,۷۵۰ t در ۴ فروش
خارج از بازه: ۱,۵۰۰ t
سهم این بازه: ۶۰.۰٪
```

---

## ✨ Features

### Reads real-world messy posts

Sellers do not write structured data. The parser handles what they actually type:

| They write | Bot reads |
|---|---|
| `۷۰۰t` · `700t` · `700 t` | 700 |
| `۱,۲۰۰t` | 1,200 |
| `1.5t` | 1,500 *(decimal = thousands)* |
| `۸۵۰تی` · `۳۰۰ ت` | 850 · 300 |
| `۶ شهریور۱۴۰۵` · `6 شهریور 1405` · `1405/06/06` | 6 Shahrivar 1405 |
| `۵ شهریور` *(no year)* | current Jalali year |

Persian **and** Arabic-Indic digits, Arabic/Persian letter variants (`ي` → `ی`,
`ك` → `ک`), zero-width non-joiners — all normalised before parsing.

### Multi-piece sales, three ways

A single post often covers several items. The bot distinguishes all three shapes:

| Post | Interpretation | Total |
|---|---|---|
| `دو تا مگنت` + `۲۰۰t` `۳۰۰t` | two prices listed → sum them | **500** |
| `دو تا مگنت` + `۲۰۰t` | one price, count in the title → multiply | **400** |
| `سنجاق سینه` + `۷۰۰t` | single item | **700** |

Counts are read in words or digits — `یه` `دو تا` `سه دونه` `۴ تا` `پنج جفت`
`دو عدد`. Two guards keep totals honest: when both prices are listed the count
never multiplies (500, not 1,000), and a stray `تا` inside prose
(`تا آخر ایونت تخفیف داشت`) is not treated as a multiplier.

### Two input paths

1. **Channel** — add the bot as an admin and every new post is recorded automatically.
2. **Forward** — forward a post (or type it) to the bot in DM; it confirms with a
   breakdown and the running book total.

Forwards carry the *origin* chat and message id, so forwarding a post the bot
already read — or forwarding it twice — updates one row instead of
double-counting. Editing a channel post updates its record too.

### Flexible date ranges

Ask in either calendar, in whatever format is convenient:

```
/sum 27 aug - 29 aug
/sum 27 august 2026 to 29 august 2026
/sum 2026-08-27 .. 2026-08-29
/sum ۵ شهریور تا ۷ شهریور
/sum 1405/06/05 - 1405/06/07
/sum today | yesterday | week | month
```

Dates typed backwards are swapped, not rejected.

### Reporting

| Command | What you get |
|---|---|
| `/sum <range>` | range total **+** grand total, amount outside the range, range share % |
| `/total` | the whole book: total, count, average sale, first/last day |
| `/report <range>` | per-day breakdown, then range total, then grand total |
| `/list <range>` | every individual sale behind a total |
| `/records [n]` | recent records with their id numbers |
| `/pending` | posts whose date could not be read |

### Deletion with confirmation

Nothing is destroyed on a single tap.

| Command | Behaviour |
|---|---|
| `/delete` | inline buttons for the last 10 sales — tap one to remove it |
| `/delete 12` | targets record #12, asks to confirm |
| `/deletemany 12 15 18` | lists the records and their combined value, then confirms |
| `/deletemany 27 aug - 29 aug` | deletes a whole window, after showing count and amount |
| `/deleteall` | wipes the book — explicit two-tap confirmation |

Every deletion reports the updated grand total.

### Robustness

- **Unreadable dates never lose money.** If a date cannot be parsed, the posting
  date is used for range queries and the record is flagged in `/pending`.
- **`/backfill`** re-runs the parser over stored raw text after a parser
  improvement, and reports how many records changed.
- **Access control.** Set `ALLOWED_CHATS` to restrict query and delete commands
  to specific chats.

---

## 🚀 Quick start

```bash
git clone https://github.com/Mahbodbe/sale-bot.git
cd sale-bot

python3 -m venv venv
venv/bin/pip install -r requirements.txt

cp .env.example .env
# put your @BotFather token in .env

venv/bin/python bot.py
```

Then add the bot to your channel as an **administrator** — read access is enough.

### Run it as a service

```bash
sudo cp sales-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sales-bot
sudo systemctl status sales-bot
```

---

## 🧪 Testing

```bash
venv/bin/pip install -r requirements-dev.txt
venv/bin/python -m pytest -q
```

**57 tests**, no network access required — amount parsing across all numeral
systems, multi-piece arithmetic, quantity-word edge cases, Jalali ↔ Gregorian
conversion, range parsing in both calendars, storage upserts, forward
de-duplication, and every deletion path.

---

## 🏗 Architecture

```
sale-bot/
├── bot.py               Telegram handlers, inline keyboards, message formatting
├── parser.py            amount / quantity / Jalali-date extraction
├── ranges.py            free-form date-range interpretation
├── store.py             SQLite schema, upserts, aggregation, deletion
├── test_parser.py       57 tests
├── requirements.txt     runtime dependencies (2)
└── sales-bot.service    systemd unit
```

Two runtime dependencies. SQLite needs no server. The parsing layer is pure
functions with no Telegram imports, which is why it is fully testable offline.

### Data model

```sql
CREATE TABLE sales (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    message_id  INTEGER NOT NULL,
    amount      INTEGER NOT NULL,   -- total for the post, in "t" units
    sale_date   TEXT,               -- ISO Gregorian; NULL when unreadable
    item        TEXT,
    event       TEXT,
    raw         TEXT NOT NULL,      -- original text, enables /backfill
    posted_at   TEXT NOT NULL,      -- fallback date
    UNIQUE (chat_id, message_id)    -- edits and forwards update, never duplicate
);
```

---

## 🔒 Security notes

- The token lives in `.env` (git-ignored, chmod `600`) — never in code or in a
  command line where it would land in shell history.
- `ALLOWED_CHATS` gates every query and delete command.
- `sales.db` is git-ignored; your sales data never leaves your server.
- Destructive commands always require an explicit confirmation tap.

---

## ⚠️ Known limitation

Telegram does not give bots access to messages sent **before** they were added to
a chat. Historical posts must be forwarded to the bot (which is fast — forward a
batch and it records them all, skipping duplicates).

---

## 📄 License

MIT — see [LICENSE](LICENSE).

---
---

<div align="center">

# 🧾 بات حساب‌داری فروش

**حساب‌داری فروش تلگرامی برای فروشندگان دست‌ساز و ایونتی**

کانال فروشت رو تبدیل کن به یه دفتر حساب. بدون اکسل، بدون جمع‌زدن دستی.

[🇬🇧 **English**](#-sale-bot) | 🇮🇷 **فارسی**

</div>

<a name="-فارسی"></a>

## مسئله چیه؟

کار دست می‌فروشی. هر فروش می‌ره توی یه کانال تلگرام به‌شکل یه پست کوتاه — اسم
کالا، قیمت، ایونت، تاریخ. آخر ماه دویست تا پست داری و یکی باید همه‌شون رو با
ماشین‌حساب بالا-پایین کنه تا جواب یه سؤال ساده رو بده: *از ۲۷ تا ۲۹ چقدر فروختیم؟*

## این بات چیکار می‌کنه؟

پست‌ها رو همون لحظه که میان می‌خونه، مبلغ و تاریخ شمسی رو درمی‌آره، و یه دفتر
جاری توی SQLite نگه می‌داره. بعد تو ازش سؤال می‌پرسی.

```
سنجاق سینه جا عینکی          ← این پست رو می‌خونه…
۷۰۰t
ایونت آلما
 ۶ شهریور۱۴۰۵
```

```
/sum ۵ شهریور تا ۷ شهریور     ← …و این رو جواب می‌ده
```

---

## ✨ قابلیت‌ها

### پست‌های واقعی و نامنظم رو می‌خونه

فروشنده داده‌ی ساخت‌یافته نمی‌نویسه. پارسر همون چیزی رو می‌فهمه که واقعاً تایپ می‌شه:

| نوشته می‌شه | خونده می‌شه |
|---|---|
| `۷۰۰t` · `700t` · `700 t` | ۷۰۰ |
| `۱,۲۰۰t` | ۱٬۲۰۰ |
| `1.5t` | ۱٬۵۰۰ *(اعشار = هزار)* |
| `۸۵۰تی` · `۳۰۰ ت` | ۸۵۰ · ۳۰۰ |
| `۶ شهریور۱۴۰۵` · `6 شهریور 1405` · `1405/06/06` | ۶ شهریور ۱۴۰۵ |
| `۵ شهریور` *(بدون سال)* | سال جاری |

ارقام فارسی **و** عربی، تفاوت حروف (`ي` ← `ی`، `ك` ← `ک`)، نیم‌فاصله — همه قبل
از پارس یکدست می‌شن.

### فروش چندقلمی، سه حالت

یه پست معمولاً چند قلم رو پوشش می‌ده. بات هر سه شکل رو تشخیص می‌ده:

| پست | تفسیر | جمع |
|---|---|---|
| `دو تا مگنت` + `۲۰۰t` `۳۰۰t` | دو قیمت نوشته شده ← جمع | **۵۰۰** |
| `دو تا مگنت` + `۲۰۰t` | یه قیمت، تعداد توی اسم ← ضرب | **۴۰۰** |
| `سنجاق سینه` + `۷۰۰t` | تک‌قلم | **۷۰۰** |

تعداد رو حرفی یا عددی می‌خونه — `یه` `دو تا` `سه دونه` `۴ تا` `پنج جفت` `دو عدد`.
دو محافظ جمع رو درست نگه می‌دارن: وقتی هر دو قیمت نوشته شده، تعداد ضرب **نمی‌شه**
(۵۰۰ نه ۱۰۰۰)، و `تا` وسط جمله‌ی عادی (`تا آخر ایونت تخفیف داشت`) به‌عنوان ضریب
حساب نمی‌شه.

### دو راه ورود اطلاعات

۱. **کانال** — بات رو ادمین کن، هر پست جدید خودکار ثبت می‌شه.
۲. **فوروارد** — پست رو برای بات فوروارد کن (یا متنش رو تایپ کن)؛ تأیید می‌فرسته
   با تفکیک مبلغ و جمع کل دفتر.

فوروارد شناسه‌ی پست *مبدأ* رو با خودش داره، پس فوروارد کردن پستی که بات قبلاً از
کانال خونده — یا دو بار فوروارد کردن — همون رکورد رو آپدیت می‌کنه، دوباره حساب
نمی‌شه. ادیت پست کانال هم رکوردش رو آپدیت می‌کنه.

### بازه‌ی زمانی انعطاف‌پذیر

با هر تقویمی و هر فرمتی که راحت‌تره بپرس:

```
/sum ۵ شهریور تا ۷ شهریور
/sum 1405/06/05 - 1405/06/07
/sum 27 aug - 29 aug
/sum 2026-08-27 .. 2026-08-29
/sum today | yesterday | week | month
/sum امروز | دیروز | هفته | ماه
```

تاریخ‌های برعکس جابه‌جا می‌شن، رد نمی‌شن.

### گزارش‌گیری

| دستور | خروجی |
|---|---|
| `/sum <بازه>` | جمع بازه **+** جمع کل دفتر، مبلغ خارج از بازه، درصد سهم بازه |
| `/total` | کل دفتر: مبلغ، تعداد، میانگین هر فروش، اولین و آخرین روز |
| `/report <بازه>` | تفکیک روزانه، بعد جمع بازه، بعد جمع کل |
| `/list <بازه>` | ریز تک‌تک فروش‌های یک بازه |
| `/records [n]` | رکوردهای اخیر با شماره‌شون |
| `/pending` | پست‌هایی که تاریخشون خوانده نشد |

### حذف با تأیید

هیچی با یه ضربه پاک نمی‌شه.

| دستور | رفتار |
|---|---|
| `/delete` | دکمه‌ی حذف برای ۱۰ فروش آخر — بزن، حذف می‌شه |
| `/delete 12` | رکورد شماره ۱۲ رو هدف می‌گیره، تأیید می‌خواد |
| `/deletemany 12 15 18` | رکوردها و مجموع مبلغشون رو نشون می‌ده، بعد تأیید |
| `/deletemany 27 aug - 29 aug` | کل یه بازه رو حذف می‌کنه، بعد از نشون‌دادن تعداد و مبلغ |
| `/deleteall` | کل دفتر رو پاک می‌کنه — تأیید صریح دومرحله‌ای |

بعد از هر حذف، جمع کل به‌روزشده رو گزارش می‌ده.

### پایداری

- **تاریخ خوانده‌نشده باعث گم‌شدن پول نمی‌شه.** اگه تاریخ پارس نشه، تاریخ ارسال
  برای بازه‌ها استفاده می‌شه و رکورد توی `/pending` علامت می‌خوره.
- **`/backfill`** بعد از بهبود پارسر، متن خام ذخیره‌شده رو دوباره پارس می‌کنه و
  می‌گه چند رکورد اصلاح شد.
- **کنترل دسترسی.** با `ALLOWED_CHATS` دستورهای پرس‌وجو و حذف رو به چت‌های
  مشخصی محدود کن.

---

## 🚀 راه‌اندازی سریع

```bash
git clone https://github.com/Mahbodbe/sale-bot.git
cd sale-bot

python3 -m venv venv
venv/bin/pip install -r requirements.txt

cp .env.example .env
# توکن @BotFather رو بذار توی .env

venv/bin/python bot.py
```

بعد بات رو **ادمین کانال** کن — دسترسی خوندن کافیه.

### اجرای دائمی

```bash
sudo cp sales-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sales-bot
```

---

## 🧪 تست

```bash
venv/bin/pip install -r requirements-dev.txt
venv/bin/python -m pytest -q
```

**۵۷ تست**، بدون نیاز به شبکه — پارس مبلغ در همه‌ی سیستم‌های عددی، حساب
چندقلمی، حالت‌های مرزی کلمات تعداد، تبدیل شمسی ↔ میلادی، پارس بازه در هر دو
تقویم، ذخیره‌سازی، ضدتکرار فوروارد، و همه‌ی مسیرهای حذف.

---

## 🏗 معماری

```
sale-bot/
├── bot.py               هندلرهای تلگرام، کیبورد اینلاین، قالب‌بندی پیام
├── parser.py            استخراج مبلغ / تعداد / تاریخ شمسی
├── ranges.py            تفسیر بازه‌ی زمانی آزاد
├── store.py             اسکیمای SQLite، ذخیره، تجمیع، حذف
├── test_parser.py       ۵۷ تست
├── requirements.txt     دو تا وابستگی
└── sales-bot.service    یونیت systemd
```

دو تا وابستگی اجرایی. SQLite سرور نمی‌خواد. لایه‌ی پارس توابع خالصه و هیچ ایمپورتی
از تلگرام نداره — همین باعث می‌شه کامل آفلاین تست بشه.

---

## 🔒 نکات امنیتی

- توکن توی `.env` می‌مونه (توی گیت نیست، پرمیشن `600`) — نه توی کد، نه توی
  کامندلاین که بره توی history شل.
- `ALLOWED_CHATS` جلوی هر دستور پرس‌وجو و حذف رو می‌گیره.
- `sales.db` توی گیت نیست؛ داده‌ی فروشت از سرور خودت بیرون نمی‌ره.
- دستورهای مخرب همیشه تأیید صریح می‌خوان.

---

## ⚠️ یه محدودیت

تلگرام به بات‌ها اجازه‌ی دیدن پیام‌های **قبل از** اضافه‌شدنشون به یه چت رو نمی‌ده.
پست‌های قدیمی باید فوروارد بشن (که سریعه — یه دسته فوروارد کن، همه رو ثبت می‌کنه
و تکراری‌ها رو رد می‌کنه).

---

## 📄 مجوز

MIT — ببینید [LICENSE](LICENSE).

<div align="center">

**ساخته [مهبد بمانی‌چم](https://github.com/Mahbodbe)** · تهران · ۱۴۰۵

</div>
