# AI HUB 2.0.4 — المحادثة واللغتان / Chat and languages

هذه وثيقة التنفيذ الحالي. **التحقق النهائي والنشر والفحص الحي معلّقة** إلى أن تُضاف النتائج إلى [ملاحظات 2.0.4](FIXES_2_0_4.md) و[سجل التحقق](VALIDATION_AR.md). النتائج السابقة تخص إصداراتها.

## العربية

الواجهة العامة عند `/` لا تتطلب بيانات المالك، وتعرض **تسعة مساعدين نشطين** وتقارير محفوظة وPDF ومحادثة بالعربية والإنجليزية. الإدارة عند `/admin` محمية. أُوقفت التسجيلات والطلبات الجديدة لمحاضر الاجتماعات وملخص واتساب والترجمة الأدبية والاستعداد للاجتماع؛ تبقى تقاريرها ومحادثات أصحابها محفوظة.

### الأخبار واللغة

تظهر أخبار NewsData في الصفحة الرئيسية وفي تبويب **الأخبار المباشرة** لسبعة مجالات. تميز الواجهة الحالة المحدّثة والمحفوظة والجارية والفارغة وفشل المصدر، وتعرض آخر تحديث ناجح وأوقات النشر والمصادر والعدد الفعلي. الحد 100 خبر مقبول من آخر 12 ساعة بعد فحص التوقيت والصلة وإزالة التكرار. دليل الخبر `provider_metadata_only`، ولا يثبت فحص صفحة الناشر أو صحة الادعاء.

النسخة المباشرة مستقلة عن التقرير وPDF. لا يعني تبدلها أن التقرير المحفوظ أُعيد بحثه. التحديث التلقائي واليدوي يخضعان لمخزن مشترك وفاصل 15 دقيقة وفترات تهدئة المصدر والرصيد اليومي، ولا يستدعيان نموذجًا أو ترجمة مدفوعة. الحد التشغيلي 20 صفحة/90 ثانية، وحجم الصفحة 10 عند عدم معرفة الخطة وحتى 50 عند تهيئتها مدفوعة؛ السقف الحالي 200 رصيد NewsData يوميًا.

اختر **العربية** أو **English** لتغيير الواجهة واتجاهها وقراءة ترجمة الأخبار المخزنة إن وُجدت. النص غير المترجم يبقى بلغة المصدر مع تنبيه صريح. تُنفذ الترجمة والتحليل بلا أدوات، ضمن عمل التقرير المحدود والمحجوز مسبقًا؛ لا تختلق الترجمة ملخصًا غائبًا أو روابط أو أوقات نشر.

في فحص 20 أكتوبر 2026 عند 18:11:09 UTC كانت أحدث مادة متاحة مؤرخة 06:10:59 UTC، خارج نافذة 12 ساعة. تعرض الواجهة احتمال التأخير والعدد المؤهل صفرًا، دون افتراض خطة مجانية أو تقديم المادة الأقدم باعتبارها حديثة. حالة المصدر منفصلة عن نجاح التقرير أو فشله.

### مساحة المساعد والتقرير

اختيار بطاقة مساعد يبدأ تقريرًا بموضوعه الافتراضي القابل للتعديل. تظهر مراحل البحث وفحص المصادر والتحليل والحفظ وإعداد PDF. تبقى آخر نسخة ناجحة متاحة مع وقتها الأصلي؛ يستعيد تحميل الصفحة آخر مهمة وخطأها الآمن إن تعثرت. توجد إعادة محاولة صريحة عند السماح بها، مع احترام مهلة الانتظار.

يُنشأ التقرير باللغة المختارة ويحفظ كل من لغته ونافذته. تغيير لغة الواجهة لا يعيد التقرير ولا يترجمه تلقائيًا. يوضح الرأس عدد الأخبار المجموعة وعدد الأخبار المحللة والسقف المطلوب؛ لا يعني جمع 100 مقال تحليلها جميعًا بالتفصيل. التاريخ والإبداع يبقيان على البحث الأصلي ومراجع موسومة بخلفية أو إلهام.

### المحادثة المتصلة

المحادثة داخل المساعد تستخدم التقرير المعروض تلقائيًا، بما فيه نسخة محفوظة تختارها؛ لا تتطلب إرفاقًا يدويًا. المحادثة العامة تسترجع تقارير مناسبة من **الجلسة نفسها**، ولا تصل إلى أرشيف الإدارة أو جلسة أخرى. سياق الخادم محدود بالنص والمصادر والنافذة المطلوبة، وليس قاعدة البيانات كلها. يمكن مناقشة أرشيف مساعد متوقف دون إعادة تفعيله.

يمكن للسؤال أن يطلب شرحًا أو مقارنة أو آثارًا أو أسئلة متابعة. إذا احتاج أدلة أحدث، يمكن للخادم بدء مهمة تقرير جديدة تحت بوابة الحصة والحجز نفسها ثم استخدام نتيجتها. لا تُقدّم الذاكرة القديمة كبحث حي، ولا يُعدّل التقرير الأصلي. اختيار الأخبار أو لغة الواجهة وحده لا ينشئ بحث نموذج.

Enter يرسل وShift+Enter يضيف سطرًا. تظهر حالة انتظار حقيقية دون بث مصطنع. الرسائل محفوظة في SQLite؛ يعيد الخادم تحميل التاريخ، وقد يختصر السياق القديم دون حذف الرسائل. لا يقبل العميل رسائل نظام أو تاريخ مساعد مزورًا. خانة «محادثات محفوظة» مجرد عنوان اختيار بقيمة فارغة، فلا تتحول إلى معرّف محادثة أو طلب شبكة.

### الخصوصية والحصص

تستخدم الجلسة العامة ملف ارتباط HttpOnly. حذفه أو الانتقال إلى متصفح آخر لا يمنح السجل القديم. تبقى المفاتيح وبيانات الإدارة في الخادم، ولا تعرض الواجهة العامة التكاليف أو تفاصيل المزود الخام.

رسائل المحادثة تشارك ميزانية الدولار ولا تستهلك حصة التقرير؛ بدء بحث جديد منها يستهلك محاولة مستقلة. القيم القائمة 100 محاولة تقرير يوميًا، و 900 دولار يوميًا و 28000 شهريًا كسقوف حماية، و 200 رصيد NewsData يوميًا. لا يرفع 2.0.4 هذه القيم ولا يصفر استخدامها. [التفصيل التشغيلي](OPERATIONS_AR.md).

عند غموض إرسال رسالة أو تقرير تُستخدم هوية المحاولة والجسم نفسيهما؛ لا إعادة مدفوعة تلقائية. فشل PDF بعد حفظ التقرير يُعالج بإعادة إخراج محلية. عند الفشل تظهر رسالة مترجمة آمنة مع مرجع ومرحلة وخيار مناسب؛ لا يُدعى وجود تقرير سابق إذا لم توجد نسخة ناجحة.

## English

The public workspace offers **nine active assistants**, saved reports, PDFs and Arabic/English chat without owner credentials. Meeting minutes, pasted WhatsApp summaries, literary translation and pre-meeting preparation are retired from active registration and new execution. Their existing owner-scoped reports, PDFs and conversations remain readable. Administration stays protected.

Seven assistants have independent NewsData feeds on the dashboard and **Live news** tab. The feed displays at most **100 accepted articles from the last 12 hours**, after timestamp, relevance and duplicate checks. The evidence level is `provider_metadata_only`: it does not establish publisher-page inspection or the truth of reported claims. Fresh, cached, loading, empty and provider-error states remain distinct, with last-successful refresh and publication/source information.

Feed refresh uses a shared cache, a 15-minute interval, provider cooldowns and a daily credit ledger. It does not call a model or automatically translate content. Retrieval is bounded to 20 pages/90 seconds; page size is capped at 10 for unknown/free configuration and 50 for configured paid access. Those settings do not verify account entitlement. The current shared credit ceiling remains 200/day.

Choose **العربية** or **English** to change controls and direction and read stored translations when available. Untranslated text remains in its source language with an explicit notice. Translation and news analysis are bounded, tool-free work covered by the explicit report reservation. Switching interface language never regenerates or translates a saved report automatically.

A report is an immutable snapshot with its own language, research window and source collection. Its collected and analyzed article counts are shown separately. Historical and creative reports retain bounded native research and labelled background/reference material. The current feed may differ from the saved report or PDF, and the interface says so.

Inline chat uses the displayed report, including a deliberately selected older version. Main chat selects relevant reports from the same anonymous session. Clients cannot supply system messages, forge assistant history or obtain another owner's reports. Discussion can explain or compare saved reports in either language without rewriting them.

A request for fresh evidence can start an explicit report job through the normal quota and reservation gate. The interface shows actual progress, including saving, and preserves safe failure reasons and references. Reconnection checks an existing operation; a new paid attempt requires an explicit retry and obeys the indicated cooldown. Enter sends; Shift+Enter inserts a line break. There is no simulated streaming.

Chat messages share the existing model budget but do not consume report attempts; fresh research started from chat does. Public UI messages omit money amounts and provider response bodies. Deleting the session cookie does not create cross-device account recovery. A failed PDF can be rendered again locally from the saved report without another research request.

## API الحالية والتوافق

| المسار | الغرض |
|---|---|
| `GET /v1/public/bootstrap` | إنشاء جلسة المتصفح |
| `GET /v1/public/workspace/agents/{id}/news?language=ar` | أخبار المجال المحفوظة؛ تحديث خلفي عند استحقاقه |
| `POST /v1/public/workspace/agents/{id}/news/refresh` | طلب تحديث `{language}` تحت القيود نفسها |
| `GET/POST /v1/public/chat/conversations` | قائمة المحادثات أو إنشاء محادثة للجلسة |
| `GET /v1/public/chat/conversations/{id}` | تاريخ المحادثة المحفوظ |
| `POST /v1/public/workspace/chat/conversations/{id}/messages` | سؤال مرتبط بالمساعد/التقرير مع `Idempotency-Key` |
| `GET /v1/public/workspace/jobs/{id}` | الحالة والمرحلة والخطأ الآمن أو التقرير |
| `GET /v1/public/workspace/reports/{id}` | النسخة المحفوظة التي تستند إليها المناقشة |

تبقى واجهات الأرشيف القديمة منفصلة. نافذة 48 ساعة وحد 20 نتيجة خام والإرفاق اليدوي السابق لا تصف مخزن 2.0.4 أو المحادثة المتصلة. راجع [README](../README.md) و[نموذج الأمان](security-model.md).
