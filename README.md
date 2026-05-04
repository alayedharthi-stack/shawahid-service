# Shawahid Service — خدمة ملف الشواهد

خدمة FastAPI مستقلة تمامًا تساعد المعلمين على تجهيز ملف الشواهد طوال العام
عبر واتساب، مع تصنيف ذكي وتصدير PDF رسمي RTL ولوحة مالك متكاملة.

> **لا تلمس هذه الخدمة كود نحلة الحالي (backend, database, dashboard) بأي شكل.**

---

## البنية السريعة

```
shawahid-service/
├── app/
│   ├── main.py                 # FastAPI entry point
│   ├── core/
│   │   ├── config.py           # Settings via env
│   │   ├── phone.py            # normalize_phone()
│   │   └── security.py         # Basic Auth for /admin
│   ├── db/base.py              # SQLAlchemy engine + session
│   ├── models/                 # Teacher, Evidence, PortfolioExport, TeacherSubscription
│   ├── schemas/                # Pydantic schemas
│   ├── services/
│   │   ├── teachers.py         # get_or_create_teacher
│   │   ├── evidences.py        # CRUD with teacher_id enforcement
│   │   ├── storage.py          # local file storage per teacher
│   │   ├── classifier.py       # OpenAI classification (with fallback)
│   │   ├── exporter.py         # HTML → PDF via Playwright
│   │   ├── subscriptions.py    # Subscription check + activation
│   │   └── whatsapp.py         # Command detection + message builders
│   ├── api/
│   │   ├── webhook.py          # POST /webhook/whatsapp, /webhook/payment
│   │   ├── teachers.py         # PATCH /teachers/me, GET/POST /teachers/{id}/...
│   │   ├── evidences.py        # PATCH/DELETE /evidences/{id}
│   │   └── admin.py            # /admin/* (Basic Auth protected)
│   ├── templates/
│   │   ├── portfolio.html      # RTL PDF template (Jinja2)
│   │   └── admin/              # Dashboard HTML pages
│   └── static/admin.css        # Nahla-inspired design tokens
├── alembic/                    # Independent migrations
├── alembic.ini
├── requirements.txt
├── Dockerfile
├── railway.toml
└── .env.example
```

---

## التشغيل المحلي

### 1. المتطلبات

- Python 3.11+
- PostgreSQL 15+
- (اختياري) OpenAI API Key للتصنيف الذكي

### 2. إعداد البيئة

```bash
cd shawahid-service

# انسخ ملف الإعدادات وعدّله
cp .env.example .env
# عدّل DATABASE_URL, ADMIN_USERNAME, ADMIN_PASSWORD, OPENAI_API_KEY
```

### 3. تثبيت المتطلبات

```bash
pip install -r requirements.txt
playwright install chromium
```

### 4. إنشاء قاعدة البيانات

```bash
# أنشئ قاعدة بيانات PostgreSQL جديدة مستقلة
createdb shawahid_db

# نفّذ migrations
alembic upgrade head
```

### 5. تشغيل السيرفر

```bash
uvicorn app.main:app --reload --port 8010
```

- API Docs: http://localhost:8010/docs
- Admin Panel: http://localhost:8010/admin/
  - Username/Password: من ملف `.env` (افتراضي: admin / change_me)
- Health: http://localhost:8010/health

---

## API Endpoints

### واتساب

```
POST /webhook/whatsapp
{
  "from_phone": "0555906901",
  "text": "نشاط تعاوني للصف السادس",
  "media_url": "https://...",
  "mime_type": "image/jpeg",
  "file_name": "photo.jpg"
}
```

**أوامر واتساب المدعومة:**
- `ملفي` — عدد الشواهد المحفوظة
- `تصدير` — إنشاء PDF (يتطلب اشتراك نشط)
- `بياناتي` — عرض بيانات المعلم
- `تعديل بياناتي` — نموذج التعبئة

### Webhook الدفع

```
POST /webhook/payment
{
  "teacher_id": 12,
  "payment_provider": "moyasar",
  "payment_reference": "txn_abc123",
  "amount_sar": 49.00
}
```

### بيانات المعلم

```
PATCH /teachers/me
{
  "phone": "966555906901",
  "name": "تركي عايد الحارثي",
  "subject": "رياضيات",
  ...
}
```

### شواهد المعلم

```
GET  /teachers/{id}/evidences
POST /teachers/{id}/export
PATCH /evidences/{id}
```

---

## الاشتراك والدفع

- كل معلم يمكنه إرسال وحفظ الشواهد مجانًا.
- تصدير PDF يتطلب اشتراكًا سنويًا بـ 49 ريال.
- رابط الدفع يُرسل عبر واتساب من تغيير `PAYMENT_LINK_TEMPLATE` في `.env`.
- عند نجاح الدفع، يُرسَل POST إلى `/webhook/payment` لتفعيل الاشتراك تلقائيًا.

---

## لوحة المالك

- الرابط: `/admin/`
- حماية: Basic Auth (ADMIN_USERNAME / ADMIN_PASSWORD)
- الصفحات:
  - `/admin/` — نظرة عامة + KPIs
  - `/admin/teachers` — قائمة المعلمين + بحث
  - `/admin/teachers/{id}` — تفاصيل + شواهد + اشتراك
  - `/admin/subscriptions` — كل الاشتراكات + إيرادات
  - `/admin/teachers/{id}/send-payment-link` — إرسال رابط الدفع
  - `/admin/teachers/{id}/export` — تصدير PDF من الإدارة
  - `/admin/evidences/{id}/edit` — تعديل شاهد
  - `/admin/evidences/{id}/delete` — حذف شاهد

---

## النشر على Railway

1. أنشئ **مشروعًا جديدًا مستقلًا** على Railway (لا تضيف لمشروع نحلة).
2. أضف **PostgreSQL** plugin وانسخ `DATABASE_URL`.
3. اضبط متغيرات البيئة في Railway:
   - `DATABASE_URL`
   - `ADMIN_USERNAME`, `ADMIN_PASSWORD`
   - `OPENAI_API_KEY`
   - `PAYMENT_LINK_TEMPLATE`
   - `BASE_URL` (رابط السيرفر على Railway)
   - `STORAGE_ROOT=/app/storage`
4. ادفع مجلد `shawahid-service` كـ root للمشروع.
5. Railway سيقرأ `railway.toml` ويشغّل Dockerfile تلقائيًا.

> **تحذير:** Storage المحلي على Railway يُفقد عند إعادة النشر.
> للإنتاج، أضف Cloudflare R2 أو AWS S3 بتعديل `storage.py` فقط.

---

## عزل المعلمين — القواعد الأساسية

1. `normalize_phone()` إلزامي قبل أي عملية.
2. لا يُنشأ أي شاهد بدون `teacher_id`.
3. كل الملفات تُحفظ تحت `storage/teachers/{teacher_id}/`.
4. كل استعلام يجب أن يتضمن `WHERE teacher_id = ?`.
5. تعديل الشواهد يتحقق من ملكية المعلم.
6. لوحة المالك محمية بـ Basic Auth فقط.

---

## أرقام المنافذ الافتراضية

| الخدمة | المنفذ |
|--------|--------|
| shawahid-service | 8010 |
| nahla-backend | 8000 |

لا تتعارض مع منافذ نحلة الحالية.
