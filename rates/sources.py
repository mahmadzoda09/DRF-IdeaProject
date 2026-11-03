"""
Курсы валют: официальный курс Нацбанка Таджикистана и курсы покупки/продажи наличных в крупных банках.
API у банков нет — берём то, что они сами показывают на сайте (JSON или таблицу в HTML).
Каждый источник независим: если сайт банка не ответил или поменял вёрстку, остальные всё равно показываются.
"""

import html
import json
import re
import threading
import urllib.request
from decimal import Decimal, InvalidOperation

from django.core.cache import cache
from django.utils import timezone

CURRENCIES = ("USD", "EUR", "RUB")
CACHE_SECONDS = 30 * 60  # банки меняют курс раз-два в день
RETRY_SECONDS = 5 * 60
TIMEOUT = 90  # загрузка идёт в фоне, можно ждать долго
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "ru"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read()


def number(value):
    try:
        return Decimal(str(value).strip().replace(",", "."))
    except InvalidOperation:
        return None


def pair(buy, sell):
    buy, sell = number(buy), number(sell)
    return {"buy": buy, "sell": sell} if buy and sell else None


def strip_tags(text):
    return html.unescape(re.sub(r"<[^>]+>", " ", text))


# ---------------------------------------------------------------- источники


def nbt():
    """Нацбанк: официальный курс на сегодня, XML. {"USD": Decimal, ...} за 1 единицу валюты."""
    today = timezone.localdate().isoformat()
    text = fetch(f"https://nbt.tj/ru/kurs/export_xml.php?date={today}&export=xmlout").decode("utf-8", "replace")
    rates = {}
    for block in re.findall(r"<Valute\b.*?</Valute>", text, re.S):
        code = re.search(r"<CharCode>(\w+)</CharCode>", block)
        nominal = re.search(r"<Nominal>([\d.]+)</Nominal>", block)
        value = re.search(r"<Value>([\d.,]+)</Value>", block)
        if code and value and code[1] in CURRENCIES:
            rates[code[1]] = number(value[1]) / (number(nominal[1]) if nominal else 1)
    return rates


def amonatbonk():
    """Амонатбонк отдаёт JSON для своего сайта; individuals — курс для физлиц."""
    data = json.loads(fetch("https://amonatbonk.tj/bitrix/templates/amonatbonk/ajax/ambApi.php"))
    group = data.get("individuals") or {}
    return {c: pair(group[c]["buy"], group[c]["sell"]) for c in CURRENCIES if c in group}


def eskhata():
    """Эсхата: таблица «Покупка и продажа»: валюта, банк покупает, банк продаёт, курс НБТ."""
    text = fetch("https://www.eskhata.com/").decode("utf-8", "replace")
    start = text.index('data-tab="rates-tab1"')
    table = text[start : text.index("</table>", start)]
    rates = {}
    for row in table.split("<tr")[1:]:
        cells = strip_tags(row).split()
        code = next((c for c in cells if c in CURRENCIES), None)
        numbers = [n for n in (number(c) for c in cells if re.fullmatch(r"[\d.,]+", c)) if n]
        if code and len(numbers) >= 2:
            rates[code] = pair(numbers[0], numbers[1])
    return rates


def spitamen():
    """Спитамен: вкладка «Нақд» (наличные) — валюта, покупка, продажа."""
    text = fetch("https://spitamenbank.tj/tj/personal").decode("utf-8", "replace")
    start = text.index('c_index="1"')
    block = text[start : text.index("</li>", start)]
    rates = {}
    for row in block.split("currency-values")[1:]:
        values = re.findall(r'c-val="([^"]+)"', row)
        if len(values) >= 3 and values[0] in CURRENCIES:
            rates[values[0]] = pair(values[1], values[2])
    return rates


def humo():
    """Хумо: курсы лежат JSON-ом внутри страницы (Next.js): {"buy":9.14,"sell":9.24,"currency":"USD"}."""
    text = fetch("https://humo.tj/").decode("utf-8", "replace").replace('\\"', '"')
    rates = {}
    for buy, sell, code in re.findall(r'"buy":([\d.]+),"sell":([\d.]+),"currency":"(\w+)"', text):
        if code in CURRENCIES and code not in rates:
            rates[code] = pair(buy, sell)
    return rates


BANKS = [
    ("amonatbonk", "Амонатбонк", amonatbonk),
    ("eskhata", "Эсхата", eskhata),
    ("spitamen", "Спитамен Бонк", spitamen),
    ("humo", "Хумо", humo),
]


def _refresh(key, source):
    """Фоновая загрузка одного источника; удачный результат хранится, пока не придёт новый."""
    try:
        rates = {c: r for c, r in source().items() if r}
    except Exception:  # сеть, таймаут, поменялась вёрстка — оставляем прошлые курсы
        rates = None
    if rates:
        cache.set(f"rates:data:{key}", {"rates": rates, "fetched_at": timezone.now()}, None)
    else:
        cache.set(f"rates:refresh:{key}", True, RETRY_SECONDS)  # неудача — попробуем раньше обычного


def _source(key, source):
    """Последние сохранённые курсы источника. Устарели или их нет — запускаем обновление в фоне, не дожидаясь его."""
    if cache.add(f"rates:refresh:{key}", True, CACHE_SECONDS):
        threading.Thread(target=_refresh, args=(key, source), daemon=True).start()
    return cache.get(f"rates:data:{key}")


def get_rates():
    """
    Сайты банков отвечают медленно (бывает 30–60 с), поэтому запрос пользователя их не ждёт:
    отдаём последние сохранённые курсы, а свежие подтягиваются в фоне раз в 30 минут.
    loading — данных ещё нет, но загрузка идёт: фронтенд спросит снова чуть позже.
    """
    official = _source("nbt", nbt)
    banks = []
    for key, name, fn in BANKS:
        data = _source(key, fn)
        banks.append(
            {
                "key": key,
                "name": name,
                "rates": data["rates"] if data else {},
                "available": bool(data),
                "fetched_at": data["fetched_at"] if data else None,
            }
        )
    times = [d["fetched_at"] for d in [official, *banks] if d and d.get("fetched_at")]
    return {
        "updated_at": max(times) if times else None,
        "loading": not official or not all(b["available"] for b in banks),
        "currencies": list(CURRENCIES),
        "nbt": official["rates"] if official else {},
        "banks": banks,
    }
