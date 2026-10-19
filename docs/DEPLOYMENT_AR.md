# AI HUB — دليل النشر على GitHub وRender

آخر مراجعة للإعدادات والمراجع: **19 أكتوبر 2026**.

هذه حزمة كود قابلة للمراجعة والنشر. لا تفترض أن مستودعًا جديدًا أو خدمة عامة باسم `ai-hub` أُنشئا بالفعل. يجب اختيار حساب GitHub وWorkspace في Render الفعليين اللذين تملكهما. لا يُستنتج حذف خدمة أو مستودع من تعذر الوصول إليه بحساب آخر.

## 1. تأكد من بنية المشروع

بعد فك الحزمة، يجب أن يكون `app/` بجانب `render.yaml` و`requirements.txt` و`agents.json`. يعتمد هذا الإصدار على الكود المباشر. لا يحتاج إلى استخراج ZIP داخل عملية البناء ولا إلى مجلد نسخ بديلة تعلو على الكود الأصلي.

من جذر المشروع، شغّل:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
python scripts/validate_blueprint.py
python scripts/package_handoff.py --output dist/AI_HUB_Project.zip
```

فاحص Blueprint يقرأ مخطط Render الرسمي ويختبر بنية YAML؛ لا ينشئ خدمة ولا يزامن حسابًا. وللعمل دون إنترنت، احفظ نسخة من [المخطط الرسمي](https://render.com/schema/render.yaml.json) ثم مرّر مسارها إلى `--schema`. إن كانت Render CLI مثبتة بالإصدار 2.7.0 أو أحدث، يمكن أيضًا تشغيل `render blueprints validate render.yaml` وفق [المرجع الرسمي](https://render.com/docs/blueprint-spec).

## 2. رفع المستودع

### مستودع جديد

أنشئ في GitHub مستودعًا **خاصًا وفارغًا** باسم `ai-hub`. الرابط التالي قالب؛ استبدل `<YOUR_OWNER>` بمالك المستودع الصحيح. لا تضع رمز وصول GitHub في الرابط.

من داخل `ai-hub`:

```bash
git init -b main
git add .gitignore .env.example .python-version README.md requirements.txt requirements-dev.txt pyproject.toml render.yaml agents.json app config docs skills tests scripts .github
git status --short
git diff --cached --stat
git commit -m "Build AI HUB: reports, budgets, scheduling and persistent storage"
git remote add origin https://github.com/<YOUR_OWNER>/ai-hub.git
git push -u origin main
```

راجع الملفات المرحّلة قبل `git commit`. يجب ألا تتضمن `.env` أو كلمات المرور أو مفاتيح API أو قواعد البيانات والتقارير الخاصة. مصادقة GitHub تتم عبر أداته أو مدير الاعتمادات في جهازك.

### مستودع قائم

اعمل في نسخة محلية من المستودع الفعلي بعد التحقق من صلاحيتك، وأنشئ فرع عمل قبل نقل الملفات. حافظ على تاريخ Git؛ لا تستخدم `force push` ولا تبدأ مستودعًا جديدًا فوق تاريخ قائم. انقل الكود المباشر والإعدادات والوثائق، ثم راجع الاختبارات وفرق التغييرات قبل دمج الفرع الذي ستتبعه خدمة Render.

### GitHub Actions

ملف `.github/workflows/tests.yml` يشغّل فحص Python والاختبارات والتحقق من Blueprint ويجهز حزمة المصدر. لا يحتاج إلى مفاتيح OpenAI أو Tavily أو Render. يحفظ Artifact باسم مرتبط بمعرّف commit لمدة 7 أيام. خطوات GitHub الرسمية المعلنة وقت الإعداد تستخدم الإصدارات الرئيسية `v7` لكل من [checkout](https://github.com/actions/checkout) و[setup-python](https://github.com/actions/setup-python) و[upload-artifact](https://github.com/actions/upload-artifact).

حُدد `autoDeployTrigger: checksPass` حتى تتبع التحديثات التي اجتازت CI. فشل فحص مخطط Render بسبب انقطاع شبكته قد يوقف النشر إلى أن يُعاد الفحص بنجاح؛ ولا يعني وحده أن كود التطبيق غير صالح.

## 3. مراجعة البنية والتكلفة قبل الإنشاء

| المكوّن | الإعداد المقترح | سبب الاختيار |
|---|---|---|
| خدمة الويب | `ai-hub`، Python، Frankfurt | خدمة واحدة للواجهة وAPI والجدولة |
| خطة الحوسبة | `0.5c-512mb` | الخطة التي كان اسمها Starter؛ 0.5 CPU و512 MB RAM |
| عدد النسخ | 1 | القرص وSQLite والجدولة المحلية يشتركون في الحالة نفسها |
| القرص الدائم | `ai-hub-data`، 1 GB | حفظ التقارير والميزانية والجداول بعد إعادة النشر |
| المسار | `/var/data/ai-hub` | مسار بيانات مستقل عن مجلد الكود |
| قاعدة البيانات | `/var/data/ai-hub/ai-hub.sqlite3` | كل الحالة الدائمة تحت القرص |

أكدت [وثائق خطط Render](https://render.com/docs/compute-plans) اسم الخطة ومواصفاتها، وتوضح [وثائق الأقراص](https://render.com/docs/disks) أن القرص يتطلب خدمة مدفوعة. لم تتوفر قيمة فاتورة مؤكدة من حسابك أثناء إعداد الملفات؛ راجع [صفحة الأسعار](https://render.com/pricing) والمبلغ المعروض في Dashboard قبل إنشاء الخدمة. تكلفة الاستضافة والقرص مستقلة عن OpenAI وTavily، ولا يوقفها حد ميزانية التطبيق.

هذا تصميم مناسب لمرحلة مالك واحد وحمل محدود. العدد 50 حد استخدام يومي؛ لا توجد نتيجة اختبار تحميل تثبت إنتاج 50 تقريرًا مكتملًا في كل يوم ضمن هذه الخطة. تتأثر السعة بطول التقارير وزمن المزودين والميزانية وجودة المصادر.

## 4. إنشاء خدمة Render

1. افتح Render واختر Workspace الذي تريد تحمّل تكلفة المشروع فيه.
2. اسمح لتكامل GitHub في Render بالوصول إلى المستودع الخاص الصحيح.
3. اختر **New → Blueprint**، ثم المستودع والفرع اللذين يحتويان `render.yaml` في الجذر.
4. راجع الخدمة الواحدة وخطة الحوسبة والقرص والمنطقة والتكلفة المعروضة.
5. أدخل القيم الخاصة لـ `APP_USERNAME` و`APP_PASSWORD` و`OPENAI_API_KEY` و`TAVILY_API_KEY` في حقول Render. لا تضعها في YAML أو ملفات المصدر.
6. اترك `SCHEDULER_ENABLED=false` عند النشر الأول.
7. بعد نجاح النشر، استخدم رابط الخدمة الذي يعرضه Render. افحص `/health`، وسجّل الدخول للواجهة، ثم راجع الإعدادات والتقارير والاستهلاك.

الحقل `sync: false` يطلب القيم أثناء إنشاء Blueprint أول مرة. عند إضافة مفتاح جديد لخدمة قائمة، أدخله يدويًا من صفحة **Environment**؛ إعادة مزامنة Blueprint لا تطلبه مجددًا. هذه آلية Render الموثقة في [قسم الأسرار](https://render.com/docs/blueprint-spec#prompting-for-secret-values).

### إعدادات الإنشاء اليدوي المكافئة

إذا استخدمت **New → Web Service** بدل Blueprint، أدخل هذه القيم صراحة:

```text
Name: ai-hub
Runtime: Python
Region: Frankfurt
Root Directory: اتركه فارغًا عندما تكون app/ في جذر المستودع
Build Command: python -m pip install -r requirements.txt
Start Command: python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1
Health Check Path: /health
Compute Plan: 0.5c-512mb
Instance Count: 1
Disk Name: ai-hub-data
Disk Mount Path: /var/data/ai-hub
Disk Size: 1 GB
```

انسخ متغيرات البيئة غير السرية من `render.yaml`، وأدخل المفاتيح بنفسك في Dashboard. لا يكفي ضبط `AI_HUB_DATABASE_PATH` دون إضافة القرص الدائم فعلًا.

## 5. ترحيل طريقة البناء السابقة

عند تحديث خدمة قائمة كانت تستخرج حزمة مضغوطة أو تستخدم ملفات بديلة أثناء البناء:

1. ارفع النسخة المباشرة بحيث يكون مسار الوحدة `app.main:app` صحيحًا من جذر الخدمة.
2. غيّر Build Command إلى `python -m pip install -r requirements.txt`، وStart Command إلى الأمر الموضح أعلاه، وأزل الاعتماد على استخراج الحزمة أثناء البناء.
3. راجع Root Directory؛ لا تتركه يشير إلى مجلد أنشأته الحزمة القديمة فقط.
4. أضف القرص المدفوع واضبط `AI_HUB_DATABASE_PATH` قبل تشغيل الجداول وحفظ بيانات الإنتاج.
5. احتفظ بنسخة أصلية من أي بيانات دائمة موجودة. هذا المشروع لا يستورد تلقائيًا تاريخ تقارير من نظام سابق غير مطابق.
6. استبدل بيانات دخول المثال بقيم خاصة. المصادقة الحالية تستخدم `APP_USERNAME` و`APP_PASSWORD`، ولا تستخدم متغير رمز الدخول القديم.
7. أعد اختبار الخدمة ثم تقريرًا حيًا واحدًا قبل تفعيل الجدولة.

ملف `.python-version` يحدد سلسلة Python 3.12. إذا كانت الخدمة تحتوي متغير `PYTHON_VERSION` قديمًا، فإنه يتقدم على الملف؛ احذفه لتطبيق اختيار الملف أو اضبطه إلى إصدار كامل منشور من Python 3.12. توضح [وثائق Render](https://render.com/docs/python-version) ترتيب الأولوية والفرق بين الطريقتين.

## 6. الديمومة وإعادة النشر

يتوفر قرص Render **للخدمة أثناء التشغيل فقط**. لا تُجرِ تهيئة SQLite في Build Command أو Pre-deploy Command؛ التطبيق يهيّئ قاعدة البيانات عند بدء التشغيل. كما لا تستطيع خدمة Cron مستقلة أو خدمة ويب ثانية قراءة هذا القرص. [مرجع الأقراص](https://render.com/docs/disks).

لا تشغّل `--workers 2` ولا عدة نسخ من التطبيق مع هذه البنية، ولا تستخدم `--reload` للإنتاج. لا توجد جدولة مستقلة في GitHub Actions؛ الجداول تعمل داخل خدمة الويب نفسها. الخدمة التي لديها قرص لا تدعم التوسع الأفقي أو النشر بلا انقطاع، وقد يتوقف الطلب الجاري أثناء استبدال النسخة. عند هذا الانقطاع يُحافظ سجل الميزانية على الاحتياطي للمحاولة الملتبسة بدل السماح بإعادتها تلقائيًا.

للانتقال مستقبلًا إلى عدة مستخدمين أو عدة نسخ، تحتاج إلى تغيير التخزين والجدولة والمصادقة معًا، مثل قاعدة بيانات مشتركة وطابور مهام؛ زيادة عدد Workers وحدها لا تحقق ذلك.
