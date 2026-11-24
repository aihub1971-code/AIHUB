# المحادثة واللغتان — AI HUB 2.1

هذا وصف للواجهة والعقود الحالية في المصدر. لا يتضمن ادعاء نشر أو نجاح فحص حي. راجع [دليل الترقية](UPGRADE_2_1.md) و[سجل التحقق](VALIDATION_AR.md).

## العربية

تعرض الصفحة الرئيسية **سبعة وكلاء**. افتح بطاقة لتستعيد محادثتك مع ذلك الوكيل. لا يبدأ فتح البطاقة أو تبديل الوكيل بحثًا أو تقريرًا، ولا توجد محادثة عامة منفصلة باسم AI HUB Chat. تظهر أدوات الأخبار والتقارير داخل المساحة عندما تفتحها.

لكل وكيل سجل مستقل محفوظ على الخادم، ويعود عند إعادة تحميل الصفحة أو الرجوع إلى الوكيل. زر **رسائل أقدم** يقرأ التاريخ السابق. الانتقال إلى وكيل آخر لا ينقل إليه مسودة الرسالة أو المحادثة نفسها. يمكن للسياق ذي الصلة من تقاريرك ومحادثاتك أن يساعد وكلاءك الآخرين، داخل مساحة الزائر نفسه فقط.

أرسل السؤال بـEnter، واستخدم Shift+Enter لسطر جديد. اقتراحات البداية تملأ المسودة دون إرسالها. تصل الإجابة تدريجيًا من المزود الحقيقي؛ لا تُحاكى الكتابة بتقطيع نص مكتمل. يُعرض النص الجزئي كما هو، وتظهر الروابط المنسقة بعد اكتمال الإجابة وفحص مصادرها. قد يحتاج سؤال عن المستجدات إلى مهمة بحث؛ عندها تظهر حالة البحث ويستأنف السؤال بعد حفظ النتيجة، دون إنشاء طلب مكرر.

### المعرفة المشتركة

افتح **المعرفة المشتركة** لإضافة تفضيل أو تصحيح أو معلومة. تبقى الإضافة بانتظار المراجعة حتى تعتمدها. يمكنك رفضها أو إزالة ملاحظة معتمدة. الملاحظات التي توافق عليها متاحة لوكلائك في المساحة نفسها؛ المقترحات غير المعتمدة والملاحظات المرفوضة لا تستخدم كذاكرة مشتركة معتمدة.

اعتماد المعلومة لا يثبت صحتها. تُعامل الملاحظات على أنها سياق قدمه صاحب المساحة، ويظل الاستشهاد بالمنشورات ومصادرها منفصلًا. حفظ الملاحظات واسترجاعها ليس تدريبًا للنموذج.

### الأخبار والتقارير

فتح أدوات الأخبار يقرأ المخزن فقط. اختر **تحديث الأخبار** للجلب الصريح، أو عدّل الموضوع واختر **إنشاء تقرير حديث**. لكل وكيل NewsData وبحث الويب ضمن حدود الخادم. سقف NewsData هو 50 نتيجة خام قبل الفحص؛ نافذة الأخبار 12 ساعة. يمكن أن يضيف بحث الويب المحدود مصادر مفحوصة عندما لا تكفي أخبار المزود، وقد يستهلك استخدام OpenAI وحصة التطبيق.

يبقى التقرير وPDF مؤرخين بنافذتهما الأصلية حتى لو تغيرت القائمة الحالية. عند استخدام خلفية أقدم أو مراجع تاريخية، تظهر تواريخها ويُوضح عدم وجود تطور حديث متحقق عند الاقتضاء. أوقات الأحداث المنقولة منفصلة عن أوقات نشر المصدر.

اختيار **العربية** أو **English** يغير لغة الواجهة واتجاهها ولغة الطلب الجديد. لا يعيد توليد التقرير المحفوظ ولا يطلق ترجمة مدفوعة بمجرد التبديل. تعرض الأخبار الترجمات المحفوظة إن وجدت؛ وإلا يبقى النص بلغة المصدر مع توضيح ذلك. كل الأوقات الكاملة تعرض بتوقيت دبي، أما تاريخ المصدر الذي لا يحمل ساعة فلا تُختلق له ساعة نشر.

### الحفظ والأخطاء

جلسة الزائر مرتبطة بملف ارتباط المتصفح؛ حذفها أو الانتقال إلى متصفح جديد لا يمنح تلقائيًا الوصول إلى السجل السابق. مساحة المالك عند `/admin/agents` تستخدم مصادقة المالك وAPI محمية، وتبقى منفصلة عن سجلات الزوار.

عند انقطاع اتصال غير مؤكد، يتيح **تحقق من المحاولة** استخدام هوية الطلب نفسها. عند إعلان الخادم فشل الإجابة، يمكن استعادة التاريخ المحفوظ لمراجعة الحالة. لا تبدأ إعادة محاولة نموذج تلقائية. تميز الواجهة بين حد استخدام التطبيق، ورصيد المزود، والتهدئة المؤقتة، وفشل الاتصال؛ لا تعرض الأسرار أو التشخيص الخام للمزود.

## English

The homepage offers seven **Agents**. Opening a card restores that agent's saved conversation and workspace without starting research or refreshing a provider. Each agent has its own durable thread and draft. There is no separate AI HUB Chat destination or homepage news feed.

Enter sends; Shift+Enter adds a line. Suggestions fill a draft. Replies stream actual provider deltas. Partial text remains plain and unlinked until a completed response passes source validation. A message that needs fresh evidence can start a research job; the interface polls saved progress and resumes the same logical request with its idempotency key.

Relevant completed conversations and cited reports can support other agents for the same visitor or owner. In **Shared knowledge**, propose a preference, correction or knowledge note, then approve or reject it. Only approved notes enter persistent shared memory. Approval is the user's decision, not independent verification or model training.

**News & reports** opens saved data. **Refresh news** and **Generate fresh report** are intentional actions. Every agent has both NewsData and web retrieval capabilities. NewsData is capped at 50 raw records before checks, with a strict 12-hour news window. Supplemental web retrieval is bounded and can incur OpenAI usage and application allowance. Older references remain dated background; a background-only analysis is still a readable report with PDF support.

Switching Arabic/English changes controls, direction and the language of the next request. It does not regenerate or automatically translate a saved report. Available cached news translations are reused; unavailable translations are clearly identified. Full timestamps use Dubai time, and date-only references stay date-only.

An ambiguous transport failure can replay the same request key. A server-declared failure remains a failed attempt that can be reviewed in saved history. The interface distinguishes application limits from provider credits and rate limits. Public sessions never grant owner access; `/admin/agents` uses authenticated `/v1/workspace/*` routes with no credential bridge in JavaScript.

See [the API and migration contract](UPGRADE_2_1.md) and [security boundaries](security-model.md).
