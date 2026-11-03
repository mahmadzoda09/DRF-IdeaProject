import re

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import HasRolePermission
from accounts.rbac import Perm, has_permission

from .context import build_context
from .groq import AssistantError, chat
from .navigation import detect_page, reply_for

RATE_LIMITED = {
    "ru": "Слишком много запросов к ИИ. Подождите минуту и попробуйте снова.",
    "tg": "Ба СИ дархостҳо зиёд шуданд. Як дақиқа сабр кунед ва боз кӯшиш кунед.",
    "en": "Too many AI requests. Please wait a minute and try again.",
}
LANGUAGE_NAMES = {"ru": "русском", "tg": "таджикском (тоҷикӣ, кириллица)", "en": "английском"}
MAX_HISTORY = 12

# Страницы фронтенда, которые ассистент может открыть: ключ (#/ключ) -> (название для модели, нужное право).
# Права те же, что у ROUTES в app.js.
PAGES = {
    "dashboard": ("Статистика", Perm.PHARMACY_STATS),
    "orders": ("Продажи / фурӯшҳо / sales — страница продаж, список чеков", Perm.ORDER_VIEW),
    "sale": ("Новая продажа — касса, «+ Продажа»; только если явно просят продать или новую продажу", Perm.ORDER_CREATE),
    "medicines": ("Лекарства", Perm.MEDICINE_VIEW),
    "expiry": ("Сроки годности — партии с истёкшим и истекающим сроком", Perm.MEDICINE_VIEW),
    "writeoff": ("Списание просроченных лекарств по месяцам срока", Perm.WRITEOFF_VIEW),
    "purchases": ("Закупки / харидҳо / purchases — страница закупок, список", Perm.PURCHASE_VIEW),
    "purchase": ("Новая закупка — «+ Закупка»; только если явно просят новую закупку", Perm.PURCHASE_CREATE),
    "suppliers": ("Поставщики", Perm.SUPPLIER_VIEW),
    "users": ("Сотрудники", Perm.USER_VIEW),
    "pharmacies": ("Аптеки", Perm.PHARMACY_VIEW),
    "reports": ("Отчёты — продажи и закупки по месяцам, скачать Excel / Word / PDF", Perm.ORDER_VIEW),
    "profile": ("Мой профиль (личный отчёт)", None),
}
OPEN_RE = re.compile(r"\[\[\s*open\s*:\s*([a-z]+)\s*\]\]", re.IGNORECASE)

SYSTEM_PROMPT = """Ты — ИИ-ассистент в «Авиценне», системе управления аптекой. Помогаешь сотрудникам: остатки и цены лекарств,
продажи, прибыль, сроки годности, что докупить, а также общие сведения о лекарствах (назначение, форма, аналоги).
Правила:
- Отвечай на {language} языке, кратко и по делу. Можно markdown: **жирный**, списки, таблицы.
- Цифры по аптеке бери только из данных ниже; если там чего-то нет — так и скажи, не выдумывай.
- Ты умеешь ОТКРЫВАТЬ страницы системы. Если пользователь просит открыть/показать раздел, перейти куда-то,
  начать продажу или закупку — открой нужную страницу: напиши одно короткое предложение («Открываю …») и в самом конце
  ответа поставь метку [[open:КЛЮЧ]]. Не объясняй, как найти раздел вручную, — просто открой. Одна метка на ответ.
  Страницы, доступные этому пользователю (КЛЮЧ — название; других нет, не придумывай):
{pages}
  Если нужной страницы нет в списке — скажи, что у пользователя нет к ней доступа.
  Пользователи часто пишут разговорным таджикским, латиницей или с ошибками. Слова «открыть/показать»:
  кушо, кушоед, вакун, нишон те/деҳ/дех, бар, бубар, гузар, open, show. Разделы: фурӯш/фурушхо/продажи → orders,
  харид/харидҳо/закупки → purchases, дору/доруҳо/лекарства → medicines, омор/статистика → dashboard,
  таъминкунанда/поставщики → suppliers, кормандон/сотрудники → users, дорухона/аптеки → pharmacies, профил → profile.
  «Страница продаж», «продажи», «сахифаи фурушхо» — это список (orders), а не касса. Кассу (sale) и новую закупку (purchase)
  открывай, только если явно сказано «новая/нав», «продать/фурӯхтан», «оформить», «+ Продажа».
  Примеры: «открой страницу продажи» → [[open:orders]]; «сахифаи фурушхора кушо» → [[open:orders]];
  «омора нишон те» → [[open:dashboard]]; «харидро кушо» → [[open:purchases]]; «фурӯши нав» → [[open:sale]];
  «хочу продать лекарство» → [[open:sale]]. Если пользователь просит показать страницу — открывай её, а не пересказывай данные.
- Сам ты данные не меняешь (не продаёшь, не закупаешь, не меняешь цены) — для этого открой нужную страницу.
- «Заканчиваются» — это малый остаток, а «скоро истекает» — срок годности; не путай их.
- О лечении и дозировках давай только общую справку и советуй консультацию врача/фармацевта; не ставь диагнозов.

Данные системы на текущий момент:
{context}"""


def allowed_pages(user):
    return {key: title for key, (title, perm) in PAGES.items() if perm is None or has_permission(user, perm)}


def split_open(reply, pages):
    """Убирает метки [[open:…]] из текста; возвращает (текст, ключ страницы или None, если метки нет или нет доступа)."""
    keys = [k.lower() for k in OPEN_RE.findall(reply)]
    text = OPEN_RE.sub("", reply).strip()
    page = next((k for k in keys if k in pages), None)
    return text, page


class MessageSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=["user", "assistant"])
    content = serializers.CharField(max_length=4000, trim_whitespace=True)


class ChatRequestSerializer(serializers.Serializer):
    messages = MessageSerializer(many=True, allow_empty=False)

    def validate_messages(self, messages):
        if messages[-1]["role"] != "user":
            raise serializers.ValidationError("Последнее сообщение должно быть от пользователя.")
        return messages[-MAX_HISTORY:]


@extend_schema(
    tags=["assistant"],
    summary="ИИ-ассистент (Groq): ответ на сообщение с учётом данных аптеки",
    description="open — ключ страницы фронтенда (#/open), которую надо открыть по просьбе пользователя, или null.",
    request=ChatRequestSerializer,
    responses=inline_serializer(
        "AssistantReply", {"reply": serializers.CharField(), "open": serializers.CharField(allow_null=True)}
    ),
)
class AssistantView(APIView):
    """Доступен всем ролям; данные в контексте — по правам пользователя."""

    permission_classes = [HasRolePermission]
    required_permissions = {"*": Perm.ASSISTANT_USE}

    def post(self, request):
        serializer = ChatRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        pages = allowed_pages(request.user)
        language = request.LANGUAGE_CODE

        # «открой …» — сразу, без модели
        page = detect_page(serializer.validated_data["messages"][-1]["content"])
        if page:
            if page in pages:
                return Response({"reply": reply_for("open", language), "open": page})
            return Response({"reply": reply_for("denied", language), "open": None})

        system = SYSTEM_PROMPT.format(
            language=LANGUAGE_NAMES.get(language, LANGUAGE_NAMES["ru"]),
            pages="\n".join(f"  {key} — {title}" for key, title in pages.items()),
            context=build_context(request.user),
        )
        try:
            reply = chat([{"role": "system", "content": system}, *serializer.validated_data["messages"]])
        except AssistantError as e:
            if e.status == 429:
                return Response({"detail": RATE_LIMITED.get(language, RATE_LIMITED["ru"])}, status=status.HTTP_429_TOO_MANY_REQUESTS)
            return Response({"detail": str(e)}, status=status.HTTP_502_BAD_GATEWAY)
        text, page = split_open(reply, pages)
        return Response({"reply": text, "open": page})
