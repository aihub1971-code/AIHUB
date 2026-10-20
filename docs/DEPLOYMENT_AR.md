# AI HUB 1.5.0 — النشر والتحديث على GitHub وRender

آخر تحديث: **20 أكتوبر 2026**. هذا دليل المصدر والإعداد؛ نتيجة النشر تُثبت للـcommit الفعلي في [سجل التحقق](VALIDATION_AR.md).

## 1. الخدمة القائمة

المصدر المباشر في [aihub1971-code/AIHUB](https://github.com/aihub1971-code/AIHUB). آخر نشر متحقق منه قبل التحديث **1.4.2** من commit `909eb177a2db74accadb0db0ec7c77580435e237` بعد PR8. كان PR9 نقطة عمل غير منشورة للمسار السابق. لا يُفترض أن مصدر 1.5.0 أو مفتاح NewsData خضعا لاختبار حي قبل تسجيله.

وافق المالك على نشر ملفات المشروع في المستودع العام وعلى **AI's workspace** (`tea-dbavcefbtjjs738oq61g`). توجد خدمة `ai-hub` بالمعرّف `srv-dbb0v3s9v7es73a9o3j0` ورابط [ai-hub-vc8w.onrender.com](https://ai-hub-vc8w.onrender.com). يُحدّث الإصدار هذه الخدمة والقرص القائمين.

| إعداد الإنتاج | القيمة |
|---|---|
| Runtime | Python 3.12؛ السلسلة مضبوطة في `.python-version` |
| الحوسبة / المنطقة | `0.5c-512mb` / Frankfurt |
| النسخ | نسخة واحدة، عامل Uvicorn واحد |
| البناء | `python -m pip install -r requirements.txt` |
| التشغيل | `python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1` |
| الفرع والتحديث | `main`؛ `autoDeployTrigger: checksPass` |
| الصحة | `/health` |
| القرص | `ai-hub-data`، 1 GB عند `/var/data/ai-hub` |
| قاعدة البيانات | `/var/data/ai-hub/ai-hub.sqlite3` |
| الجدولة | `SCHEDULER_ENABLED=false` |

يبني Render المصدر مباشرة من `app/` و`requirements.txt`. حزمة ZIP للتسليم وليست خطوة بناء. لا تُضف نسخة أو عاملًا آخر دون إعادة تصميم SQLite والجدولة. القرص متاح وقت التشغيل، لا أثناء البناء أو أوامر ما قبل النشر، وإعادة النشر قد تقطع الخدمة مؤقتًا. [مرجع القرص](https://render.com/docs/disks).

السعر المعروض عند إنشاء الخدمة كان 7 دولارات للحوسبة و0.25 دولار لقرص 1 GB شهريًا. هذا سجل إعداد سابق، وليس عرض سعر جديدًا؛ راجع [أسعار Render](https://render.com/pricing). رسوم الاستضافة واشتراك NewsData منفصلة عن ميزانية OpenAI.

## 2. إضافة مفتاح NewsData بأمان

أخبار حرب إيران وحدها تنتقل إلى NewsData.io. يُرسل المفتاح في رأس `X-ACCESS-KEY` من الخادم؛ لا يوضع في JavaScript أو روابط الطلبات أو المستودع. تحتفظ الخدمة بـ`OPENAI_API_KEY` للترجمة العربية وبقية المساعدين والمحادثة.

1. افتح الخدمة القائمة في Render ثم **Environment**.
2. أضف `NEWSDATA_API_KEY` داخل الحقل الخاص. لا تنسخه إلى المحادثة أو سجل تشخيص.
3. راجع القيم غير السرية: `NEWSDATA_PLAN=unknown`, `NEWSDATA_PAGE_SIZE=10`, `NEWSDATA_DAILY_CREDIT_LIMIT=200`.
4. احتفظ ببيانات دخول المالك وOpenAI الصالحة. لا تستبدل بقية متغيرات الخدمة أثناء إضافة المفتاح.
5. فعّل القيم بالنشر المناسب. **Save only** يحفظها دون تشغيلها حتى النشر التالي؛ **Save and deploy** يعيد نشر البناء القائم بالقيم الجديدة، و**Save, rebuild, and deploy** يبني وينشر. طابق الخطوة مع commit 1.5.0 المقصود. [مرجع Environment](https://render.com/docs/configure-environment-variables).

**مهم للخدمة الموجودة:** يحتوي `render.yaml` على `NEWSDATA_API_KEY` مع `sync: false`. هذا يطلب السر عند إنشاء Blueprint أول مرة فقط؛ تتجاهله المزامنات اللاحقة، لذلك يجب إضافة المفتاح يدويًا للخدمة الحالية. تُدار القيم غير السرية في YAML مع القيم المطابقة في Environment لمنع التعارض عند المزامنة. [مرجع أسرار Blueprint](https://render.com/docs/blueprint-spec#prompting-for-secret-values).

الخطة `unknown` مجرد وصف حتى تتأكد من الحساب. الخطة المجانية تؤخر الأخبار 12 ساعة وتسمح حتى 10 مقالات للصفحة؛ لا ترفع حجمها إلى 50 دون خطة تدعمه. لا يُستخدم الأرشيف أو `timeframe` المدفوع في هذا الإصدار. [حدود NewsData](https://newsdata.io/blog/pricing-plan-in-newsdata-io/).

## 3. الإعداد والترحيل

تبقى `MAX_REPORTS_PER_DAY=50` و`DAILY_BUDGET_USD=5` و`MONTHLY_BUDGET_USD=100` والمجدول متوقفًا. يُسجل رصيد NewsData مستقلًا؛ لا تعني حدود الدولار تحديد فاتورة اشتراكه.

خذ نسخة SQLite متسقة قبل تحديث الخدمة، وانقلها إلى وجهة خاصة خارج قرصها. تُهيأ البيانات الإضافية عند بدء التطبيق مع الحفاظ على التقارير والمحادثات والجداول السابقة. احفظ القرص ومسار القاعدة؛ لا تستبدل القاعدة أو تصفر الحصص أثناء التحديث. [خطوات النسخ والاستعادة](OPERATIONS_AR.md#7-الديمومة-والنسخ-الاحتياطي).

تحتاج إعدادات أول تثبيت إلى أربع قيم خاصة: `APP_USERNAME`, `APP_PASSWORD`, `OPENAI_API_KEY`, `NEWSDATA_API_KEY`. يمكن لأخبار NewsData الإنجليزية أن تبقى متاحة دون ترجمة عند غياب OpenAI، لكن بقية المساعدين والمحادثة تحتاجه.

## 4. التحقق قبل النشر وبعده

من جذر المصدر، داخل البيئة المحلية:

```bash
python -m pytest
node tests/test_ui_news.js
node tests/test_ui_chat.js
python scripts/validate_blueprint.py
python scripts/package_handoff.py --output dist/AI_HUB_Project.zip
```

الاختبارات الاصطناعية لا تستخدم مفاتيح إنتاج. بعد نجاح CI للـcommit المحدد، تابع Render وطابق commit مع `/health` ونسخة `1.5.0`. فحص الصحة لا يختبر المفتاح أو جودة المقالات.

فحص الخدمة دون توليد، مع بيانات المالك في البيئة الخاصة:

```bash
python scripts/smoke_test.py --base-url https://ai-hub-vc8w.onrender.com
```

لجلب صفحة واحدة بعد إعداد المفتاح، برصيد NewsData وترجمة OpenAI عند تهيئتها:

```bash
python scripts/smoke_test.py --base-url https://ai-hub-vc8w.onrender.com --live --agent iran-news
```

راجع الأخبار الحقيقية وروابطها وناشريها وتواريخها، والترجمة وحالة فشلها إن وجدت، ثم تبديل العربية والإنجليزية دون إعادة طلب. اختبر التالي كصفحة جديدة والسابق كعرض محفوظ، وافحص الرصيد وديمومة البيانات. عند انتهاء المهلة راجع المحاولة القائمة قبل إنشاء معرّف جديد. لا تُعد طلبًا تلقائيًا.

يسجل قائد الإصدار نتائج الفحص الفعلية في `docs/VALIDATION_AR.md`. هذه الأوامر إجراءات تشغيل، وليست ادعاء بتنفيذها أو نجاحها. يبقى المجدول متوقفًا حتى اعتماد مواعيده واختبار المسار الحي.
