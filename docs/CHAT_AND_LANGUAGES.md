# AI HUB 1.5.1 — المحادثة واللغتان / Chat and languages

تصف الوثيقة المصدر 1.5.1؛ حالة النشر والاختبار الفعلي في [سجل التحقق](VALIDATION_AR.md).

## العربية

الواجهة العامة عند `/` متاحة دون تسجيل دخول، وتضم 13 مساعدًا والمحادثة بالعربية والإنجليزية؛ يرسل Enter البحث. لا تعرض التكاليف أو السجلات الإدارية. تبقى لوحة المالك عند `/admin`، وتبقى مسارات `/v1/*` الإدارية خلف مصادقة المالك، باستثناء المسارات العامة المستقلة تحت `/v1/public/*`. لا يتطلب هذا الفصل مفاتيح أو إعدادات خدمة جديدة.

اختر **العربية** أو **English** أعلى الصفحة لتغيير لغة الواجهة واتجاهها. في أخبار حرب إيران يعرض التبديل النص العربي أو الإنجليزي المحفوظ، دون طلب جديد. تُجهز ترجمة العناوين والأوصاف مسبقًا عند جلب كل صفحة، على دفعات حتى خمسة مقالات، حتى لو بدأت باللغة الإنجليزية. عند تعذر ترجمة مادة، تظهر الإنجليزية الأصلية مع توضيح ذلك. لا يُخترع وصف مفقود.

افتح **التقارير والمساعدون** واختر **أخبار حرب إيران**. يجلب هذا المساعد مقالات إنجليزية من NewsData.io بالبحث الثابت `iran war` ونافذة آخر48ساعة. لا تعود قائمة الناشرين الستة القديمة قيدًا لهذا المسار. **التالي** يجلب صفحة إضافية ويحفظها، و**السابق** يفتح صفحة محفوظة. يقتصر البحث على أول 20 نتيجة مزود قبل الفحص، بصفحات افتراضية حتى 10، ثم يتوقف التالي؛ قد يقل عدد الأخبار المقبولة عن 20. صفحة جديدة تعني محاولة ورصيدًا جديدين؛ الرجوع إلى المحفوظ لا يعيد البحث.

الخطة المجانية تؤخر المقالات12ساعة وتسمح حتى10مقالات في الصفحة. يعرض التقرير فترة التغطية وحالة الخطة؛ كتابة يوم قديم في المهمة لا تجعل هذا المساعد بحثًا أرشيفيًا. يظهر كل خبر بعنوان ووصف متاح وناشر وتاريخ وزر يفتح رابط المقال الأصلي. [حدود NewsData](https://newsdata.io/blog/pricing-plan-in-newsdata-io/).

لبقية المساعدين، اكتب الموضوع والفترة المطلوبة في مربع المهمة. تستخدم التقارير الجديدة اللغة المختارة، ويمكن حفظ لغة مستقلة لكل جدول. التقارير القديمة غير الثنائية تبقى بلغتها الأصلية.

افتح **المحادثة** في الواجهة العامة أو من لوحة الإدارة بعد تسجيل الدخول للمساعدة في اختيار مساعد أو صياغة مهمة أو فهم تقرير. ابدأ محادثة جديدة لموضوع مستقل، أو عُد إلى محادثة محفوظة. اضغط Enter للإرسال وShift+Enter لسطر جديد. لإرفاق تقرير، افتحه ثم اختر إرفاقه صراحة؛ يظهر اختياره ويمكن إزالته. يحصل النموذج على سياق محدود منه، لا على قاعدة البيانات كلها.

المحادثة تقدم ردود OpenAI متعددة الأدوار؛ لا تجري بحثًا حيًا ولا تغير الإعدادات أو تشغل المساعدين بنفسها. تظهر حالة انتظار حقيقية دون محاكاة بث النص. ليس هذا وعدًا بتطابق كل خصائص ChatGPT.

ترتبط تقارير الزائر ومحادثاته بجلسة متصفحه، ولا تعرض أرشيف الإدارة أو جلسة زائر آخر. تستخدم الواجهة العامة ملف جلسة HttpOnly؛ حذف ملف الجلسة أو الانتقال إلى متصفح آخر لا يمنح الوصول إلى السجل السابق. تُحفظ الرسائل على قرص SQLite الدائم. حد الرسالة8000حرف، وسياق النموذج حتى20دورًا مكتملًا ضمن100000بايت؛ قد تُستبعد رسائل قديمة من سياق النموذج مع بقائها محفوظة. يحفظ المتصفح اختيار اللغة فقط؛ تسجيل الخروج يمسح المحتوى الخاص ويمنع الردود المتأخرة من إعادته.

تستخدم المحادثة `OPENAI_API_KEY` على الخادم. تشارك التقارير والترجمة ميزانية OpenAI اليومية والشهرية، لكنها لا تستهلك حصة50محاولة تقرير. رصيد NewsData منفصل، ويخص صفحات الأخبار الجديدة. عند غموض إرسال رسالة، يحافظ تكرار المفتاح والجسم نفسيهما على المحاولة القائمة دون إرسال مدفوع جديد؛ المعرّف الجديد طلب جديد.

## English

The public interface at `/` needs no owner login and offers all 13 assistants and chat in Arabic and English. Enter submits a search. Public pages and API responses omit accounting information. Owner administration remains at `/admin`, and all administrative `/v1/*` endpoints still require owner authentication; `/v1/public/*` is the separate anonymous-session API. No new environment settings are required.

Use **العربية** or **English** in the page header. Iran War News pages contain the original English text and Arabic translations prepared when each page is retrieved. Translation runs in tool-free batches of up to five articles, even when the interface is English. Switching language displays saved text without another API request. If translation is unavailable, the original English remains visible; a missing description is not replaced with invented facts.

Open **Reports and assistants**, choose **Iran War News**, and retrieve a page. NewsData.io receives the fixed `iran war` query (AND semantics), original language English and the latest48-hour window. This assistant no longer uses the old six-publisher restriction. **Next** retrieves and saves another page; **Previous** reads a saved report. Each new page consumes a report attempt and one NewsData credit. Each search stops after the first 20 provider results, before validation, with a default page size of 10. Fewer valid articles may be shown; Next stops at the limit or when results end.

Free NewsData articles are delayed12hours and pages contain at most10articles. A date typed in the task does not turn this flow into archive search. Each article shows its headline, available description, publisher, publication time and original link. Other assistants retain their task controls and OpenAI research behavior; their archived reports keep their original language.

Open **Chat** for help with platform controls, task wording or a report you explicitly attach. Conversations and completed turns are saved on the server. Public reports and conversations belong to that browser's anonymous session and do not expose the administrator's history or another visitor's session. Enter sends; Shift+Enter adds a line break. The assistant receives bounded recent history and selected report context. It has no live search or platform-operation tools and cannot change settings or run reports itself.

Chat waits for a real OpenAI response; it does not simulate streaming. Owner login state remains in memory, and logout clears private administration content. The browser never receives either provider key. New chat messages use OpenAI USD accounting, separately from NewsData credits, and do not consume the report-attempt quota.

## Authenticated API

All endpoints in the table below require the existing owner authentication. Public UI calls use the separate `/v1/public/*` prefix, scoped to an anonymous browser session and without accounting fields. JSON schemas reject unknown fields. The server loads conversation history itself; clients cannot submit forged system or assistant history.

| Endpoint | Purpose |
|---|---|
| `GET /v1/chat/conversations` | List the owner's conversations, with pagination |
| `POST /v1/chat/conversations` | Create a conversation using optional `title` and `language` |
| `GET /v1/chat/conversations/{id}` | Read saved messages and turn status |
| `POST /v1/chat/conversations/{id}/messages` | Send `{message, language, report_id}`; `Idempotency-Key` is required |
| `POST /v1/agents/{id}/run` | Create a report using `{task, language}`; Iran continuation may add a saved `continue_from_report_id` |
| `POST /v1/schedules` | Create a schedule including optional `language`, default `ar` |
| `PATCH /v1/schedules/{id}` | Change the saved schedule language or other schedule fields |
| `GET /v1/usage` | Shared OpenAI USD, separate report/chat totals and NewsData credit accounting |

The message response includes the saved assistant message, conversation and turn IDs, accounting and context diagnostics. One pending turn per conversation is permitted. A duplicate key/body replays the saved response or identifies its existing pending/failed turn. A changed message, language or selected report with the same key is rejected. A failed or interrupted paid attempt is retained with its conservative reservation; there is no automatic provider retry.

The migration creates chat tables and adds `schedules.language` with default `ar` for existing schedules. It preserves existing reports, daily quotas, report idempotency hashes for Arabic, and the disabled-by-default scheduler. Back up the SQLite database using the existing backup script before operational changes.
