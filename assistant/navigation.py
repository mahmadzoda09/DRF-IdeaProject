"""
Команды «открой страницу» распознаются без модели: быстро, надёжно и не тратит лимит Groq.
Понимает русский, таджикский (в т.ч. без таджикских букв и латиницей) и английский.
"""

import re

# таджикские буквы -> русские, чтобы «фурӯш», «фуруш» и «furush» сводились к одному
_TG = str.maketrans("ӯҳқғҷӣё", "ухкгчие")
_LAT = [
    ("kh", "х"), ("sh", "ш"), ("ch", "ч"), ("zh", "ж"), ("ya", "я"), ("yu", "ю"), ("x", "х"), ("h", "х"),
    ("a", "а"), ("b", "б"), ("d", "д"), ("e", "е"), ("f", "ф"), ("g", "г"), ("i", "и"), ("j", "ч"),
    ("k", "к"), ("l", "л"), ("m", "м"), ("n", "н"), ("o", "о"), ("p", "п"), ("q", "к"), ("r", "р"),
    ("s", "с"), ("t", "т"), ("u", "у"), ("v", "в"), ("y", "й"), ("z", "з"), ("w", "в"), ("c", "к"),
]


def normalize(text):
    text = text.lower().translate(_TG)
    # английские команды проверяем до транслитерации
    english = text
    for lat, cyr in _LAT:
        text = text.replace(lat, cyr)
    return f"{english} {text}"


# «открой» — однозначно навигация; «покажи» — только вместе со словом «страница» или в очень короткой фразе
OPEN = ("откр", "перейд", "перейт", "кушо", "кушод", "вакун", "бубар", "гузар", "open", "go to", "navigate")
SHOW = ("покаж", "показ", "нишон", "show")
PAGE = ("страниц", "раздел", "сахиф", "сахф", "бахш", "page", "section", "tab")
NEW = ("нов", "нав", "new", "оформ")

# порядок важен: «отчёт о продажах» раньше «продажи», «новая продажа» раньше «продажи», «дорухона» раньше «дору»
SECTIONS = [
    ("writeoff", ("списан", "спиши", "хисоббарор", "write-off", "writeoff", "write off")),  # раньше «хисоб» у отчётов
    ("reports", ("отчет", "хисобот", "хисоб", "report")),
    ("expiry", ("мухлат", "срок", "годност", "expir")),  # раньше «дору»: «мӯҳлати доруҳо» — страница сроков
    ("sale", ("продат", "фурухтан", "касс", "sell", "+ прод")),
    ("chat", ("чат", "переписк", "chat", "message")),  # раньше аптек: «чат аптеки»
    ("pharmacies", ("аптек", "дорухон", "pharmac")),
    ("orders", ("прода", "фуруш", "sales", "order", "чек")),
    ("purchases", ("закуп", "харид", "purchase")),
    ("medicines", ("лекарст", "препарат", "дору", "medicine", "drug")),
    ("dashboard", ("омор", "статист", "dashboard", "stat")),
    ("suppliers", ("поставщ", "таъминкун", "таминкун", "supplier")),
    ("users", ("сотрудн", "кормандон", "корманд", "персонал", "staff", "employee", "user")),
    ("profile", ("профил", "кабинет", "profile")),
]

REPLIES = {
    "open": {"ru": "Открываю страницу.", "tg": "Саҳифаро мекушоям.", "en": "Opening the page."},
    "denied": {
        "ru": "У вас нет доступа к этой странице.",
        "tg": "Шумо ба ин саҳифа дастрасӣ надоред.",
        "en": "You don't have access to this page.",
    },
}


def _has(text, words):
    return any(w in text for w in words)


def detect_page(message):
    """Ключ страницы, если сообщение — просьба открыть раздел; иначе None (тогда отвечает модель)."""
    text = normalize(message)
    words = len(message.split())
    wants_open = _has(text, OPEN) or _has(text, PAGE) or (_has(text, SHOW) and words <= 3)
    if not wants_open or words > 12:
        return None
    for key, stems in SECTIONS:
        if _has(text, stems):
            # «новая продажа / хариди нав» — касса и форма закупки, иначе списки
            if key == "orders" and _has(text, NEW):
                return "sale"
            if key == "purchases" and _has(text, NEW):
                return "purchase"
            return key
    return None


def reply_for(kind, language):
    return REPLIES[kind].get(language, REPLIES[kind]["ru"])
