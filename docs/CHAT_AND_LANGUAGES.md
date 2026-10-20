# AI HUB 1.4 — Chat and language guide / دليل المحادثة واللغتين

## العربية

اختر **العربية** أو **English** من أعلى المنصة. يتغير اتجاه الصفحة وأسماء المساعدين والأزرار والرسائل. تغيير اللغة وحده لا يرسل طلبًا إلى OpenAI ولا يعيد توليد تقرير محفوظ. عند طلب تقرير جديد، تُرسل اللغة المختارة إلى الخادم وتُحفظ معه. يمكن حفظ لغة مستقلة لكل جدول يومي.

افتح **المحادثة** بعد تسجيل الدخول، ثم اكتب سؤالك. يساعدك AI HUB Chat في اختيار المساعد المناسب، وصياغة طلب التقرير، وفهم التغطية والتواريخ والاستخدام. يفتح زر محادثة جديدة موضوعًا مستقلًا، ويمكن الرجوع إلى المحادثات السابقة من القائمة. اضغط Enter للإرسال وShift+Enter لسطر جديد.

لمناقشة تقرير محفوظ، افتحه أولًا ثم اختر إرفاق التقرير في المحادثة. يظهر التقرير المختار بوضوح ويمكن إزالته. يتلقى النموذج محتوى محدودًا من التقرير الذي اخترته مع الرسالة؛ لا يحصل على قاعدة البيانات كاملة. يمكن طلب شرح التقرير أو تلخيصه أو ترجمته دون تغيير نسخته المحفوظة.

المحادثة الحالية تقدم إجابات نصية متعددة الأدوار عبر OpenAI Responses API. لا تُنفذ إجراءات في الحساب ولا تُجري بحثًا حيًا من نافذة المحادثة؛ تُرشدك إلى مساعد الأخبار أو المساعد المتخصص لتنفيذ البحث. ليست وعدًا بتطابق جميع أدوات أو سلوك واجهة ChatGPT. أثناء الانتظار تظهر حالة فعلية، ولا تُعرض كتابة وهمية أو إجابة نموذجية مكان رد النموذج.

يحفظ الخادم الرسائل وسجل المحادثة في SQLite على قرص Render الدائم. يُرسل أحدث سياق مكتمل ضمن حدود الحجم إلى OpenAI للإجابة؛ قد تُحذف الرسائل الأقدم من سياق النموذج دون حذفها من الأرشيف. حد الرسالة 8000 حرف، وسياق المحادثة حتى 20 دورًا مكتملًا ضمن 100000 بايت. يحتفظ المتصفح باختيار اللغة فقط، ولا يحفظ كلمة المرور أو رسائل المحادثة في localStorage. تسجيل الخروج يمسح المحتوى الخاص من الصفحة ويمنع ردًا متأخرًا من إظهاره.

تستخدم المحادثة مفتاح `OPENAI_API_KEY` والنموذج المضبوطين أصلًا على الخادم. لا تطلب منك إدخال مفتاح آخر. تُحجز تكلفة الدور قبل إرساله وتُسجل بصورة مستقلة، لكنها تشارك التقارير ميزانية اليوم والشهر. رسائل المحادثة لا تدخل في حد 50 محاولة تقرير يوميًا. عند غموض الاتصال يمكن إعادة الطلب نفسه بمعرّفه دون دفعه مرة ثانية؛ طلب دور جديد بمعرّف جديد يظل عملية جديدة.

## English

Use the language switch at the top to change the entire interface between Arabic and English. The choice controls the language of new reports and can be stored with a daily schedule. Switching the interface does not make a paid API call or translate an existing report automatically; archived reports retain their original language and reading direction.

Open **Chat** after signing in. Ask for help choosing an assistant, writing a good report request, understanding dates and coverage, or interpreting usage. Conversations and their completed messages are saved on the server and can be reopened. Enter sends; Shift+Enter inserts a line break.

To discuss a report, open it and explicitly attach it in Chat. You can ask for an explanation, summary or translation while keeping the saved original. The assistant receives bounded context from the selected report and recent completed conversation turns. It cannot browse the live web or operate platform controls from Chat; use the named specialist assistant for new research.

The interface waits for an actual OpenAI response. It does not simulate token streaming. The conversation history is durable, while login state remains in memory and private content is cleared on logout. OpenAI requests use `store:false`; AI HUB keeps its own database history. The server key is never placed in browser JavaScript.

## Authenticated API

All endpoints require the existing owner authentication. JSON schemas reject unknown fields. The server loads conversation history itself; clients cannot submit forged system or assistant history.

| Endpoint | Purpose |
|---|---|
| `GET /v1/chat/conversations` | List the owner's conversations, with pagination |
| `POST /v1/chat/conversations` | Create a conversation using optional `title` and `language` |
| `GET /v1/chat/conversations/{id}` | Read saved messages and turn status |
| `POST /v1/chat/conversations/{id}/messages` | Send `{message, language, report_id}`; `Idempotency-Key` is required |
| `POST /v1/agents/{id}/run` | Create a report using `{task, language}`; language defaults to `ar` |
| `POST /v1/schedules` | Create a schedule including optional `language`, default `ar` |
| `PATCH /v1/schedules/{id}` | Change the saved schedule language or other schedule fields |
| `GET /v1/usage` | Shared USD totals and separate report/chat breakdowns |

The message response includes the saved assistant message, conversation and turn IDs, accounting and context diagnostics. One pending turn per conversation is permitted. A duplicate key/body replays the saved response or identifies its existing pending/failed turn. A changed message, language or selected report with the same key is rejected. A failed or interrupted paid attempt is retained with its conservative reservation; there is no automatic provider retry.

The migration creates chat tables and adds `schedules.language` with default `ar` for existing schedules. It preserves existing reports, daily quotas, report idempotency hashes for Arabic, and the disabled-by-default scheduler. Back up the SQLite database using the existing backup script before operational changes.
