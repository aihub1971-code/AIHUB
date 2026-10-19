# AI HUB

منصة Python / FastAPI خاصة بمالك واحد لإدارة 13 مساعدًا، إعداد تقارير الأخبار، وحفظ النتائج والاستهلاك في قاعدة SQLite. اسم المشروع هو **AI HUB**، والمستودع الفعلي هو [aihub1971-code/AIHUB](https://github.com/aihub1971-code/AIHUB). اسم خدمة Render المقترح يبقى `ai-hub`.

## ما الذي تتضمنه النسخة؟

- واجهة عربية لتسجيل الدخول وتشغيل المساعدين، مع حفظ التقارير ومراجعة الاستهلاك.
- اتصال مباشر بـ OpenAI API باستخدام `gpt-6.1-sol` افتراضيًا، وبحث المصادر عبر Tavily.
- تقارير الأخبار تستهدف 6 أخبار موثقة؛ عند نقص الأدلة يُعلن النقص ولا تُختلق أخبار لإكمال العدد.
- سجل دائم للمحاولات والنتائج وتقدير التكلفة، مع حد عالمي أقصى قدره 50 محاولة في اليوم بحسب توقيت دبي.
- ميزانية افتراضية قدرها 5 دولارات يوميًا و100 دولار شهريًا لطلبات المزودين عبر التطبيق. هذه حدود تشغيل مقترحة، وليست فاتورة أو ضمانًا لإنتاج 50 تقريرًا ضمنها.
- جدولة يومية داخل عملية التطبيق، متوقفة افتراضيًا حتى ضبط المفاتيح واختبار تقرير حقيقي.
- إعداد Render لخدمة Python واحدة وقرص دائم 1 GB، واختبارات GitHub Actions وحزمة تسليم نظيفة.

الملفات تتضمن التكامل والاختبارات المحلية. تم التحقق من الوصول إلى مستودع GitHub العام المذكور أعلاه، وأظهر التكامل صلاحيات القراءة والدفع والإدارة (`pull` و`push` و`admin`). لا توجد حاجة إلى طلب إذن جديد لقراءة ملفاته. لم يُنفذ اختبار OpenAI أو Tavily حي ولم تُنشر خدمة Render؛ يتطلب الاختبار الحي مفاتيحك الخاصة وصلاحية استخدام النموذج ورصيد المزودين.

### حالة المصدر والمراجعة

عند فحص `main` بالمعرّف `69123a04672300807c63b4b8a68d3c9029f453d3`، كان المستودع يحتوي `AI_HUB_Project.zip` وملفّي التسليم وREADME تمهيديًا. الحزمة المرفوعة مطابقة بايتًا ببايت لحزمة المصدر المختبرة. تضع هذه التغييرات `app/` و`render.yaml` و`requirements.txt` وبقية المصدر مباشرة في جذر المستودع، حتى يبني Render المشروع دون استخراج ZIP أثناء البناء. فرع التجهيز هو `ai-hub/prepare-render-source`؛ تُراجع حالة طلب الدمج ونتائج GitHub Actions في المستودع قبل اعتماد التغييرات للنشر.

وافق المالك في 19 أكتوبر 2026 على نشر ملفات المشروع والوثائق في هذا المستودع العام، وعلى استخدام مساحة Render **AI's workspace** (`tea-dbavcefbtjjs738oq61g`). يمكن متابعة إعداد الخدمة في هذه المساحة؛ حالة النشر الفعلية تُراجع من Render.

اجتازت نسخة التسليم 126 اختبار Python، وفحصًا للواجهة على الكمبيوتر والهاتف ببيانات اصطناعية. راجع [سجل التحقق وحدود الاختبار](docs/VALIDATION_AR.md) للتفاصيل وحالة الربط البعيد.

## ابدأ من هنا — Windows PowerShell

افتح PowerShell في جذر شجرة المصدر حيث يقع `app/` بجانب `render.yaml`؛ عند استخراج الحزمة يكون ذلك داخل مجلد `ai-hub`. أوامر التشغيل التالية تخص المصدر المباشر المجهّز للمراجعة، ولا تعمل من صفحة `main` التي تحتوي ZIP وحده. استخدم Python 3.12:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

ضع قيمة عشوائية طويلة في `APP_PASSWORD` داخل `.env`، واختر `APP_USERNAME`. أضف `OPENAI_API_KEY` و`TAVILY_API_KEY` بنفسك. لا ترسل المفاتيح في المحادثة ولا ترفع `.env` إلى GitHub. اترك `SCHEDULER_ENABLED=false` في البداية.

```powershell
python -m pytest
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

افتح [الواجهة المحلية](http://127.0.0.1:8000) وسجّل الدخول. تستخدم الواجهة `APP_USERNAME` و`APP_PASSWORD`؛ مفتاح OpenAI مخصص للخادم ولا يُدخل في شاشة تسجيل الدخول.

### macOS / Linux quickstart

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
# Edit .env privately before continuing.
python -m pytest
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Run the server from the repository root. The runtime dependencies are pinned in `requirements.txt`; test and deployment-validation tools are in `requirements-dev.txt`. SQLite and the scheduler use Python's standard library. The `.python-version` file keeps Render on the Python 3.12 release line.

## التحقق من الاتصال

مع تشغيل التطبيق، افتح نافذة طرفية ثانية وفعّل البيئة الافتراضية فيها:

```bash
python scripts/smoke_test.py
```

الفحص الافتراضي يقرأ حالة الخدمة وقائمة المساعدين فقط. لإعداد **تقرير حي واحد** عبر مفاتيحك بعد ضبطها، وهو طلب قد يترتب عليه استهلاك:

```bash
python scripts/smoke_test.py --live --agent iran-news
```

لفحص خدمة منشورة، مرّر رابطها الحقيقي إلى `--base-url`. اترك المصادقة داخل `.env` المحلي؛ لا تكتب كلمة المرور داخل الرابط أو أمر الطرفية. راجع سجل التقارير قبل إعادة طلب انتهت مهلته، لأن المزود قد يكون نفذه.

## النشر والتشغيل

اقرأ [دليل النشر على GitHub وRender](docs/DEPLOYMENT_AR.md)، ثم [دليل التشغيل والميزانية والجدولة](docs/OPERATIONS_AR.md).

إعداد الإنتاج المرفق:

| الإعداد | القيمة |
|---|---|
| الخدمة | Web Service واحدة باسم `ai-hub` |
| Runtime | Python 3.12 |
| Compute plan | `0.5c-512mb`، الاسم السابق Starter |
| المنطقة | Frankfurt |
| Build command | `python -m pip install -r requirements.txt` |
| Start command | `python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1` |
| فحص الصحة | `/health` |
| القرص الدائم | 1 GB عند `/var/data/ai-hub` |
| قاعدة البيانات | `/var/data/ai-hub/ai-hub.sqlite3` |
| الجدولة عند النشر الأول | متوقفة |

الخطة والقرص مدفوعان. راجع السعر النهائي في شاشة Render قبل إنشاء الخدمة. يتطلب القرص نسخة تشغيل واحدة؛ إعادة النشر قد تقطع الخدمة لفترة قصيرة. [مرجع Render للقرص الدائم](https://render.com/docs/disks) و[خطط الحوسبة](https://render.com/docs/compute-plans).

## الملفات المهمة

| الملف / المجلد | الغرض |
|---|---|
| `app/` | API والواجهة ومحرك التقارير والتخزين والجدولة |
| `agents.json` | سجل المساعدين الـ13 وبصمات مهاراتهم |
| `skills/` | 14 ملف تعليمات؛ مساعد محاضر الاجتماعات له ملفان |
| `config/permissions.json` | صلاحيات الأدوات لكل مساعد |
| `.env.example` | أسماء الإعدادات وقيم آمنة نموذجية |
| `render.yaml` | البنية المقترحة للنشر على Render |
| `tests/` | اختبارات دون مفاتيح إنتاج |
| `scripts/` | فحص الخدمة، النسخ الاحتياطي، فحص Blueprint، وتجهيز ZIP |
| `docs/` | النشر والتشغيل والأمان ومصدر التسليم |

## حزمة تسليم جديدة

```bash
python scripts/package_handoff.py --output dist/AI_HUB_Project.zip
```

تحتوي الحزمة على الكود والإعدادات النموذجية والوثائق والاختبارات. يستبعد المُجهّز قواعد البيانات والمفاتيح وملفات البيئة الفعلية والسجلات وبيئات Python المؤقتة. لا تُستخدم الحزمة المضغوطة في أمر البناء؛ يشغّل Render ملفات `app/` مباشرة من المستودع.

للتفاصيل الأمنية وحدود المرحلة ذات المالك الواحد، راجع [نموذج الأمان](docs/security-model.md).
