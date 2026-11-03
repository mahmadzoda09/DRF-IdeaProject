from django.test import SimpleTestCase
from rest_framework.test import APITestCase

from accounts.models import User

from .models import Medicine, Pharmacy, Supplier
from .sqlite_unicode import unicode_like


class UnicodeLikeTest(SimpleTestCase):
    def test_like_pattern(self):
        self.assertTrue(unicode_like("%нурофен%", "Нурофен 200 мг"))
        self.assertTrue(unicode_like("%ПАРАЦЕТАМОЛ%", "Парацетамол"))
        self.assertTrue(unicode_like("%елка%", "Ёлка"))  # «ё» = «е»
        self.assertTrue(unicode_like("н_рофен", "Нурофен"))
        self.assertFalse(unicode_like("нурофен", "Нурофен 200"))  # без % — целиком
        # экранированные % и _ — обычные символы
        self.assertTrue(unicode_like("%50\\%%", "скидка 50%", "\\"))
        self.assertFalse(unicode_like("%50\\%%", "скидка 500", "\\"))
        self.assertIsNone(unicode_like("%a%", None))


class CyrillicSearchTest(APITestCase):
    """Поиск в API не зависит от регистра и для кириллицы (на SQLite LIKE так не умеет)."""

    def setUp(self):
        pharmacy = Pharmacy.objects.create(name="A")
        user = User.objects.create_user("director", password="pass12345", role=User.Role.DIRECTOR, pharmacy=pharmacy)
        Medicine.objects.create(pharmacy=pharmacy, title="Нурофен 200 мг", price="35.00")
        Medicine.objects.create(pharmacy=pharmacy, title="Парацетамол 500 мг", price="5.00")
        Supplier.objects.create(pharmacy=pharmacy, name="Шифо-Фарм")
        self.client.force_authenticate(user)

    def count(self, url, query):
        return self.client.get(url, {"search": query}).data["count"]

    def test_case_insensitive(self):
        for query in ["Нурофен", "нурофен", "НУРОФЕН", "нУрО"]:
            self.assertEqual(self.count("/api/medicines/", query), 1, query)
        self.assertEqual(self.count("/api/medicines/", "МГ"), 2)
        self.assertEqual(self.count("/api/medicines/", "аспирин"), 0)
        self.assertEqual(self.count("/api/suppliers/", "шифо"), 1)


class MedicineExpiryTest(APITestCase):
    """Срок годности остатка: партии по FIFO (продаётся старое), ближайший срок, фильтр и сортировка; видят все сотрудники."""

    def setUp(self):
        from datetime import timedelta

        from django.utils import timezone

        from purchases.models import Purchase, PurchaseItem

        self.today = timezone.localdate()
        self.day = day = lambda n: self.today + timedelta(days=n)  # noqa: E731
        pharmacy = Pharmacy.objects.create(name="Моя")
        supplier = Supplier.objects.create(pharmacy=pharmacy, name="Шифо")
        self.pharmacist = User.objects.create_user("ph", password="x", pharmacy=pharmacy, role=User.Role.PHARMACIST)

        def med(title, quantity, batches):
            m = Medicine.objects.create(pharmacy=pharmacy, title=title, price=10, quantity=quantity)
            for n, (qty, expire) in enumerate(batches):
                p = Purchase.objects.create(pharmacy=pharmacy, supplier=supplier, total_price=0)
                Purchase.objects.filter(pk=p.pk).update(created_at=timezone.now() - timedelta(days=100 - n))
                PurchaseItem.objects.create(purchase=p, medicine=m, quantity=qty, price=5, expire_date=expire)
            return m

        # старая партия (истекает через 10 дней) уже продана: остаток 30 — это новая партия
        self.sold_old = med("Старое продано", 30, [(20, day(10)), (30, day(200))])
        # остаток 40 = вся новая (30) + 10 из старой → ближайший срок у старой
        self.mixed = med("Смешанный остаток", 40, [(20, day(10)), (30, day(200))])
        self.expired = med("Просроченный", 5, [(5, day(-3))])
        self.no_stock = med("Нет на складе", 0, [(10, day(5))])
        self.no_purchase = med("Без закупок", 7, [])
        self.client.force_authenticate(self.pharmacist)

    def by_title(self, **params):
        r = self.client.get("/api/medicines/", params)
        self.assertEqual(r.status_code, 200, r.data)
        return {m["title"]: m for m in r.data["results"]}, [m["title"] for m in r.data["results"]]

    def test_fifo_expiry_and_status(self):
        meds, _ = self.by_title()
        self.assertEqual(meds["Старое продано"]["expire_date"], str(self.day(200)))
        self.assertEqual(meds["Старое продано"]["expire_status"], "ok")
        self.assertEqual(meds["Смешанный остаток"]["expire_status"], "soon")
        self.assertEqual([b["quantity"] for b in meds["Смешанный остаток"]["batches"]], [10, 30])
        self.assertEqual(meds["Просроченный"]["expire_status"], "expired")
        self.assertIsNone(meds["Нет на складе"]["expire_date"])
        self.assertIsNone(meds["Без закупок"]["expire_status"])

    def test_filter_and_ordering(self):
        self.assertEqual(sorted(self.by_title(expiry="soon")[1]), ["Смешанный остаток"])
        self.assertEqual(self.by_title(expiry="expired")[1], ["Просроченный"])
        self.assertEqual(sorted(self.by_title(expiry="attention")[1]), ["Просроченный", "Смешанный остаток"])
        order = self.by_title(ordering="expiry")[1]
        self.assertEqual(order[:3], ["Просроченный", "Смешанный остаток", "Старое продано"])
        self.assertEqual(self.client.get("/api/medicines/", {"expiry": "x"}).status_code, 400)

    def test_expiry_page_batches(self):
        r = self.client.get("/api/medicines/expiry/")
        self.assertEqual(r.status_code, 200)
        rows = [(b["title"], b["quantity"], b["status"]) for b in r.data["batches"]]
        # ближайший срок первым; проданная старая партия и лекарство без остатка не попадают
        self.assertEqual(rows[0], ("Просроченный", 5, "expired"))
        self.assertEqual(rows[1], ("Смешанный остаток", 10, "soon"))
        self.assertNotIn("Нет на складе", [b["title"] for b in r.data["batches"]])
        self.assertEqual(r.data["summary"]["expired"], {"batches": 1, "quantity": 5})
        self.assertEqual(r.data["summary"]["soon"], {"batches": 1, "quantity": 10})
        self.assertEqual(r.data["summary"]["total"], {"batches": 4, "quantity": 75})


class MedicineInStockFilterTest(APITestCase):
    """in_stock=1 — только лекарства с остатком (целые упаковки или блистеры вскрытой)."""

    def test_in_stock(self):
        pharmacy = Pharmacy.objects.create(name="Склад")
        user = User.objects.create_user("st", password="x", pharmacy=pharmacy, role=User.Role.PHARMACIST)
        for title, quantity, open_blisters in [("Есть", 3, 0), ("Блистер", 0, 1), ("Нет", 0, 0)]:
            Medicine.objects.create(pharmacy=pharmacy, title=title, price=10, quantity=quantity, blisters=3, open_blisters=open_blisters)
        self.client.force_authenticate(user)
        titles = lambda params: sorted(m["title"] for m in self.client.get("/api/medicines/", params).data["results"])  # noqa: E731
        self.assertEqual(titles({"in_stock": 1}), ["Блистер", "Есть"])
        self.assertEqual(titles({"search": "Нет"}), ["Нет"])  # поиск находит и закончившиеся


class PharmacyStaffCountTest(APITestCase):
    """Админ видит у каждой аптеки, сколько в ней сотрудников."""

    def test_staff_count(self):
        admin = User.objects.create_superuser("adm", password="x")
        busy, empty = Pharmacy.objects.create(name="Б"), Pharmacy.objects.create(name="П")
        for name in ("d", "p1", "p2"):
            User.objects.create_user(name, password="x", pharmacy=busy)
        self.client.force_authenticate(admin)
        counts = {p["name"]: p["staff_count"] for p in self.client.get("/api/pharmacies/").data["results"]}
        self.assertEqual(counts, {"Б": 3, "П": 0})
        created = self.client.post("/api/pharmacies/", {"name": "Новая"}).data
        self.assertEqual(created["staff_count"], 0)
