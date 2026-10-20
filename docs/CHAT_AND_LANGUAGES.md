# AI HUB 2.0 — المحادثة واللغتان / Chat and languages

تصف الوثيقة مصدر 2.0 قبل إثبات نشره. حالة الاختبار والنشر الفعلي في [سجل التحقق](VALIDATION_AR.md).

## العربية

الواجهة العامة عند `/` لا تتطلب اسم مستخدم أو كلمة مرور، وتعرض المساعدين الـ13 ومساحة تقارير ومحادثة بالعربية والإنجليزية. تبقى إدارة المالك عند `/admin` محمية، ولا تعرض الجلسة العامة التكاليف أو سجلات الإدارة.

اختر **العربية** أو **English** لتغيير واجهة الاستخدام واتجاهها. يُنشأ التقرير باللغة المختارة عند طلبه، وتحفظ كل نسخة لغتها ووقت بحثها. تبديل لغة الواجهة لا يعيد البحث ولا يترجم تقريرًا محفوظًا تلقائيًا. يمكن مناقشة النسخة أو طلب شرحها باللغة الأخرى دون تعديل أصلها المحفوظ.

### مساحة المساعد

اختيار مساعد يفتح موضوعه الافتراضي القابل للتعديل وآخر تقرير ناجح ومهمة التحديث. تعرض المساحة الأخبار وملف الإحاطة، أو المصادر ودليل القراءة للتاريخ، أو المراجع واللوحة الإبداعية للقصة. تُحفظ كل نسخة وPDF خاص بها؛ فتح نسخة أقدم أو تنزيلها لا يعيد الاتصال بالمزود.

الأخبار الجديدة تأتي من OpenAI web_search وفحص صفحات الناشرين، خلال نافذة **12 ساعة** مجمدة بوقت نشر كامل موثق، وبحد **20 بعد التحقق والصلة وإزالة التكرار**. لا تستخدم هذه المساحة NewsData أو قائمة ناشرين ثابتة. التاريخ والإبداع يسمحان بمراجع أقدم موسومة بوضوح؛ محاضر الاجتماعات وواتساب والترجمة تعمل من النص المقدم دون وصول إلى حسابات أو تسجيلات.

### المحادثة المتصلة

المحادثة داخل مساحة المساعد تستخدم التقرير المعروض تلقائيًا، بما فيه نسخة محفوظة اخترتها؛ لا تتطلب خطوة إرفاق يدوي. المحادثة العامة تسترجع تقارير مناسبة من **جلسة المستخدم نفسها**، ولا تصل إلى أرشيف المالك أو جلسة أخرى. يتحدد السياق في الخادم، ويتضمن نص التقرير ومصادره ونافذته ضمن حدود واضحة، وليس قاعدة البيانات كلها.

يمكن للمحادثة شرح النتائج أو مقارنتها أو استخلاص آثارها أو صياغة أسئلة متابعة. عندما يتطلب السؤال معلومات أحدث، يمكن للخادم إنشاء مهمة بحث جديدة عبر بوابة الحجز والحصة نفسها. تظهر مراحل حقيقية ثم تستخدم الإجابة التقرير الناتج؛ لا تُقدَّم الذاكرة القديمة كبحث حي، ولا يُعاد كتابة التقرير السابق.

اضغط Enter للإرسال وShift+Enter لسطر جديد. الإرسال يستخدم ردًا حقيقيًا مع حالة انتظار؛ لا يوجد بث مصطنع. الرسائل والمحادثات محفوظة في SQLite. يعيد الخادم تحميل التاريخ بنفسه، وقد يختصر السياق القديم مع بقاء الرسائل محفوظة. العميل لا يزوّد رسائل نظام أو تاريخ مساعد مزورًا.

### الخصوصية والتكلفة

الجلسة العامة تستخدم ملف ارتباط HttpOnly مع عزل التقارير والمحادثات وPDF. حذف ملف الجلسة أو استخدام متصفح آخر لا يمنح الوصول إلى السجل القديم. مفاتيح OpenAI وبيانات الإدارة تبقى في الخادم.

تشارك الرسائل ميزانية الدولار مع التقارير، لكنها لا تستهلك حصة **50 محاولة تقرير يوميًا**. أي بحث جديد تنشئه المحادثة يستهلك محاولة تقرير مستقلة. سقوف نشر 2.0 المستهدفة **900 دولار يوميًا و28000 شهريًا** هي حدود حماية محافظة، لا فواتير متوقعة؛ [التفصيل](OPERATIONS_AR.md).

عند غموض إرسال رسالة، احتفظ بالمفتاح والجسم نفسيهما للتحقق من المحاولة؛ لا ينفذ الخادم إعادة مدفوعة تلقائية. ظهور فشل PDF لا يعني فشل التقرير؛ يمكن إعادة إخراجه محليًا من النسخة نفسها.

## English

The public workspace at `/` offers all 13 existing assistants, saved reports, PDFs and chat without owner credentials. Administration remains protected at `/admin`. Public responses omit costs, provider keys and administrative records.

Choose **العربية** or **English** for the interface. A report is generated in its selected language and saved as an immutable snapshot with its research window. Changing interface language does not refetch or automatically translate an existing report. Chat can explain that saved version in another language without rewriting it.

Inline chat automatically uses the report displayed in the active assistant workspace, including an older version deliberately selected from history. No manual attachment step is required. Main chat can retrieve relevant saved reports belonging to the same anonymous session. Server-side context selection enforces ownership and bounds; clients cannot submit system messages or forged assistant history.

A question requiring newer information can start an explicit background research job through the normal quota and reservation gate. The interface shows actual progress, and the answer uses the resulting report. Existing reports remain unchanged. Normal chat answers use a real pending state rather than simulated streaming. Enter sends and Shift+Enter inserts a line break.

New briefing news uses OpenAI live web research and publisher-page publication verification in a frozen **12-hour** window, with at most **20 items after validation and deduplication**. Historical and creative references are labelled background/reference material. The current workspace does not use NewsData or the old fixed publisher list.

Chat messages share USD budgets but do not consume the 50-report-attempt quota; new research initiated from chat does. Matching idempotency keys and request bodies identify the same operation without an automatic paid retry. Deleting the anonymous session cookie does not create a recoverable cross-device account.

## API الحالية والتوافق

| المسار | الغرض |
|---|---|
| `GET /v1/public/bootstrap` | إنشاء جلسة المتصفح العامة |
| `GET/POST /v1/public/chat/conversations` | قائمة المحادثات أو إنشاء محادثة مملوكة للجلسة |
| `GET /v1/public/chat/conversations/{id}` | التاريخ المحفوظ وحالات الأدوار |
| `POST /v1/public/workspace/chat/conversations/{id}/messages` | السؤال ولغته والمساعد/التقرير المحددان، مع `Idempotency-Key` |
| `GET /v1/public/workspace/jobs/{id}` | متابعة بحث بدأ من مساحة المساعد أو المحادثة |
| `GET /v1/public/workspace/reports/{id}` | التقرير المحفوظ الذي تستند إليه المناقشة |

تبقى `/v1/chat/*` المحمية ومسارات 1.x القديمة للتوافق. توجيهات الإرفاق اليدوي وNewsData ونافذة 48 ساعة تخص تلك التجربة السابقة فقط، وليست طريقة عمل مساحة 2.0. راجع [README](../README.md) و[نموذج الأمان](security-model.md).
