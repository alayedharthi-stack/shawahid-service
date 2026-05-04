# Railway Deployment Guide — Shawahid Service

> هذه الخدمة **مستقلة تمامًا** عن نحلة. أنشئ مشروع Railway منفصل.

---

## الخطوة 1 — رفع الكود على GitHub

ارفع مجلد `shawahid-service` فقط على repo منفصل أو كـ subdirectory:

```bash
# Option A: مجلد منفصل داخل نفس الـ repo
# Railway سيعمل من الـ root path الذي تحدده في إعدادات المشروع

# Option B: repo مستقل تمامًا (الأفضل)
git init shawahid-service-repo
cd shawahid-service-repo
cp -r /path/to/shawahid-service/* .
git add . && git commit -m "Initial shawahid service"
git remote add origin https://github.com/USERNAME/shawahid-service.git
git push -u origin main
```

---

## الخطوة 2 — إنشاء مشروع Railway

1. افتح [railway.app](https://railway.app) → **New Project**
2. اختر **Deploy from GitHub repo**
3. اختر الـ repo (أو الـ monorepo مع تحديد مجلد `shawahid-service` في Root Directory)
4. Railway سيكتشف `Dockerfile` تلقائيًا

---

## الخطوة 3 — إضافة PostgreSQL

1. داخل نفس مشروع Railway → **New** → **Database** → **PostgreSQL**
2. انتظر حتى يبدأ الـ database
3. اذهب إلى **Variables** في خدمة الـ Web وأضف:

```
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

Railway يربط المتغير تلقائيًا من الـ Postgres service.

---

## الخطوة 4 — إضافة Volume (Persistent Storage)

1. داخل الـ Web Service → **Settings** → **Volumes**
2. **Add Volume**:
   - Mount Path: `/app/storage`
   - Size: 5 GB (كافٍ للـ MVP)
3. أضف المتغير:

```
STORAGE_DIR=/app/storage
```

---

## الخطوة 5 — إعداد Environment Variables

في Railway → Web Service → **Variables** → أضف:

```
# Database (auto-linked from Postgres plugin)
DATABASE_URL=${{Postgres.DATABASE_URL}}

# Storage (must match Volume mount path)
STORAGE_DIR=/app/storage

# Admin Panel
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<كلمة_سر_قوية_لا_تقل_عن_16_حرف>

# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_CLASSIFIER_MODEL=gpt-4o-mini
OPENAI_EXPORT_MODEL=gpt-4o-mini
OPENAI_TIMEOUT_SECONDS=30

# Service
APP_ENV=production
PUBLIC_BASE_URL=https://<railway-domain>.up.railway.app

# Payment (minimal for MVP)
PAYMENT_LINK_TEMPLATE=https://your-payment-link.com/teacher/{teacher_id}
PAYMENT_PROVIDER=manual
PAYMENT_SUCCESS_SECRET=<سر_عشوائي>

# WhatsApp (اتركها فارغة حتى تربط لاحقًا)
WHATSAPP_VERIFY_TOKEN=
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
```

> **ملاحظة:** لا تضع `OPENAI_API_KEY` في `git` أو في أي ملف يُرفع.

---

## الخطوة 6 — النشر

1. Railway سيبدأ الـ build تلقائيًا عند push جديد
2. `start.sh` سيُنفّذ `alembic upgrade head` قبل تشغيل الـ server
3. تابع الـ logs من Railway Dashboard

**مدة Build المتوقعة:** 5-10 دقائق (بسبب Playwright Chromium)

---

## الخطوة 7 — اختبار الـ Deployment

### 7.1 Health Check
```bash
curl https://<domain>/health
```
المتوقع:
```json
{"ok": true, "service": "shawahid-service", "env": "production"}
```

### 7.2 إرسال شاهد نصي
```bash
curl -X POST https://<domain>/webhook/whatsapp \
  -H "Content-Type: application/json" \
  -d '{
    "from_phone": "0555906901",
    "text": "نشاط تعاوني للصف السادس في مادة الرياضيات",
    "media_url": null,
    "mime_type": null,
    "file_name": null
  }'
```
المتوقع:
```json
{"ok": true, "teacher_id": 1, "evidence_id": 1, "message": "تم حفظ الشاهد بنجاح"}
```

### 7.3 اختبار عزل المعلمين
```bash
# المعلم الأول
curl -X POST https://<domain>/webhook/whatsapp \
  -H "Content-Type: application/json" \
  -d '{"from_phone": "0555906901", "text": "توزيع منهج رياضيات"}'

# المعلم الثاني (رقم مختلف)
curl -X POST https://<domain>/webhook/whatsapp \
  -H "Content-Type: application/json" \
  -d '{"from_phone": "0555000000", "text": "خطة درس لغة عربية"}'

# تحقق من شواهد المعلم الأول فقط
curl https://<domain>/teachers/1/evidences
# يجب ألا تظهر شواهد المعلم الثاني
```

### 7.4 اختبار بيانات المعلم
```bash
curl -X PATCH https://<domain>/teachers/me \
  -H "Content-Type: application/json" \
  -d '{
    "phone": "0555906901",
    "name": "تركي عايد الحارثي",
    "subject": "رياضيات",
    "stage": "الابتدائية",
    "grades": "الرابع، الخامس، السادس",
    "school_name": "مدرسة تحفيظ القرآن الكريم الابتدائية بقيا",
    "principal_name": "الأستاذ إبراهيم جدعان العمري"
  }'
```

### 7.5 تفعيل اشتراك (محاكاة webhook الدفع)
```bash
curl -X POST https://<domain>/webhook/payment \
  -H "Content-Type: application/json" \
  -d '{
    "teacher_id": 1,
    "payment_provider": "manual",
    "payment_reference": "test-001",
    "amount_sar": 49.0
  }'
```
المتوقع: `{"ok": true, "status": "active", ...}`

### 7.6 اختبار التصدير (بعد تفعيل الاشتراك)
```bash
curl -X POST https://<domain>/teachers/1/export
```

### 7.7 اختبار أوامر واتساب
```bash
# "ملفي"
curl -X POST https://<domain>/webhook/whatsapp \
  -H "Content-Type: application/json" \
  -d '{"from_phone": "0555906901", "text": "ملفي"}'

# "بياناتي"
curl -X POST https://<domain>/webhook/whatsapp \
  -H "Content-Type: application/json" \
  -d '{"from_phone": "0555906901", "text": "بياناتي"}'

# "تصدير" (يتطلب اشتراك نشط)
curl -X POST https://<domain>/webhook/whatsapp \
  -H "Content-Type: application/json" \
  -d '{"from_phone": "0555906901", "text": "تصدير"}'
```

### 7.8 Admin Panel
```
افتح: https://<domain>/admin/
أدخل: ADMIN_USERNAME / ADMIN_PASSWORD
```
تحقق من:
- [ ] قائمة المعلمين تظهر
- [ ] صفحة التفاصيل تعمل
- [ ] حالة ai_status لكل شاهد ظاهرة
- [ ] زر إعادة التصنيف (🤖) يعمل
- [ ] portfolio-json يعمل: `GET /admin/teachers/1/portfolio-json`
- [ ] export يبدأ من Admin
- [ ] صفحة الاشتراكات تعمل

---

## استكشاف الأخطاء

### Build فشل — Playwright
إذا فشل `playwright install --with-deps chromium` في Docker:
```dockerfile
# بديل: استخدم صورة Playwright جاهزة
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy
```
ثم احذف السطر `RUN playwright install --with-deps chromium` من Dockerfile.

### Migration فشلت
تحقق من `DATABASE_URL` في Railway Variables.
من داخل Railway shell:
```bash
alembic current
alembic upgrade head
```

### PDF فارغ / Arabic خاطئة
تأكد من تثبيت `fonts-noto` في Dockerfile (موجود بالفعل).

### StaticFiles Error عند startup
تأكد من أن Volume مُربط على `/app/storage` قبل النشر.

---

## بعد النجاح — ربط دومين

1. Railway Dashboard → Web Service → **Settings** → **Domains**
2. أضف: `shawahid.nahlah.ai` أو `admin.shawahid.nahlah.ai`
3. أضف CNAME في DNS يشير إلى Railway domain
4. حدّث `PUBLIC_BASE_URL` في Variables

---

## ملاحظات أمان

- لا تشارك `ADMIN_PASSWORD` مع أحد.
- لا تضع `OPENAI_API_KEY` في `git`.
- Storage paths دائمًا تحت `/app/storage/teachers/{teacher_id}/` فقط.
- كل endpoint في `/admin/*` يتطلب Basic Auth.
- webhook الدفع في MVP بدون توقيع — أضف `PAYMENT_SUCCESS_SECRET` header check لاحقًا.
