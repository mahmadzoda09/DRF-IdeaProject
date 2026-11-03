import json
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from pharmacies.models import Pharmacy

from . import sources

# урезанные копии того, что отдают сайты
NBT_XML = """<?xml version="1.0" encoding="windows-1251" ?><ValCurs Date="2026-11-01">
<Valute ID="840"><CharCode>USD</CharCode><Nominal>1</Nominal><Name>Доллар США</Name><Value>9.2086</Value></Valute>
<Valute ID="810"><CharCode>RUB</CharCode><Nominal>10</Nominal><Name>Рубль</Name><Value>1.144</Value></Valute>
<Valute ID="156"><CharCode>CNY</CharCode><Nominal>1</Nominal><Name>Юань</Name><Value>1.3844</Value></Valute>
</ValCurs>"""
AMONAT_JSON = json.dumps(
    {"individuals": {"USD": {"buy": 9.14, "sell": 9.23}, "EUR": {"buy": 10.05, "sell": 10.25}}, "legal": {"USD": {"buy": 1, "sell": 1}}}
)
ESKHATA_HTML = """<div class="tabs__container" data-tab="rates-tab1"><table cellspacing="0">
<tr><th>Валюта</th><th>Банк покупает </th><th>Банк продает</th><th>Курс НБТ</th></tr>
<tr><td><span><svg><path d="M1 2"/></svg> USD </span></td><td><span> 9.1400 </span></td><td><span> 9.2400 </span></td><td><span> 9.2165 </span></td></tr>
<tr><td><span><svg></svg> RUB </span></td><td><span> 0.1030 </span></td><td><span> 0.1050 </span></td><td><span> 0.1144 </span></td></tr>
</table></div><table><tr><td>USD</td><td>1</td><td>1</td></tr></table>"""
SPITAMEN_HTML = """<li c_index="0"><div class="conversation__row currency-values">
<div c-val="USD"></div><div c-val="9.2086"></div><div c-val="9.2086"></div></div></li>
<li c_index="1"><div class="conversation__row currency-values">
<div class="conversation__td" c-val="USD"><b>USD</b></div>
<div class="conversation__td" c-val="9.1500"><span>9.1500</span></div>
<div class="conversation__td" c-val="9.2400"><span>9.2400</span></div></div></li>"""
HUMO_HTML = r"""<script>self.__next_f.push([1,"{\"currencyRates\":[{\"id\":51,\"buy\":9.14,\"sell\":9.24,\"currency\":\"USD\"},{\"id\":52,\"buy\":10.26,\"sell\":10.36,\"currency\":\"EUR\"}]}"])</script>"""

PAGES = {
    "nbt.tj": NBT_XML,
    "amonatbonk.tj": AMONAT_JSON,
    "eskhata.com": ESKHATA_HTML,
    "spitamenbank.tj": SPITAMEN_HTML,
    "humo.tj": HUMO_HTML,
}


def fake_fetch(url):
    for host, body in PAGES.items():
        if host in url:
            return body.encode()
    raise AssertionError(url)


class SyncThread:
    """Фоновая загрузка в тестах выполняется сразу."""

    def __init__(self, target, args, daemon):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


@mock.patch("rates.sources.fetch", fake_fetch)
class ParsersTest(TestCase):
    def test_parsers(self):
        self.assertEqual(sources.nbt(), {"USD": Decimal("9.2086"), "RUB": Decimal("0.1144")})  # за 1 рубль, без CNY
        self.assertEqual(sources.amonatbonk()["USD"], {"buy": Decimal("9.14"), "sell": Decimal("9.23")})
        self.assertEqual(sources.eskhata(), {"USD": {"buy": Decimal("9.14"), "sell": Decimal("9.24")}, "RUB": {"buy": Decimal("0.103"), "sell": Decimal("0.105")}})
        self.assertEqual(sources.spitamen(), {"USD": {"buy": Decimal("9.15"), "sell": Decimal("9.24")}})  # наличные, не курс НБТ
        self.assertEqual(sources.humo()["EUR"], {"buy": Decimal("10.26"), "sell": Decimal("10.36")})


@mock.patch("rates.sources.threading.Thread", SyncThread)
class RatesApiTest(TestCase):
    def setUp(self):
        cache.clear()
        pharmacy = Pharmacy.objects.create(name="Моя")
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user("ph", password="x", pharmacy=pharmacy, role=User.Role.PHARMACIST))

    def test_rates_for_every_user(self):
        with mock.patch("rates.sources.fetch", fake_fetch):
            r = self.client.get("/api/rates/")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["loading"])
        self.assertEqual(r.data["nbt"]["USD"], "9.2086")
        self.assertEqual([b["name"] for b in r.data["banks"] if b["available"]], ["Амонатбонк", "Эсхата", "Спитамен Бонк", "Хумо"])
        self.assertEqual(APIClient().get("/api/rates/").status_code, 401)

    def test_bank_down_keeps_others_and_old_rates(self):
        with mock.patch("rates.sources.fetch", fake_fetch):
            self.client.get("/api/rates/")

        def broken(url):
            if "eskhata" in url:
                raise TimeoutError
            return fake_fetch(url)

        cache.delete("rates:refresh:eskhata")  # пора обновить, но сайт не отвечает
        with mock.patch("rates.sources.fetch", broken):
            r = self.client.get("/api/rates/")
        eskhata = next(b for b in r.data["banks"] if b["key"] == "eskhata")
        self.assertTrue(eskhata["available"])  # остались прошлые курсы
        self.assertEqual(eskhata["rates"]["USD"]["sell"], "9.2400")

        cache.clear()
        with mock.patch("rates.sources.fetch", broken):
            r = self.client.get("/api/rates/")
        self.assertTrue(r.data["loading"])
        self.assertFalse(next(b for b in r.data["banks"] if b["key"] == "eskhata")["available"])
