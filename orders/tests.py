from datetime import datetime
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from orders.models import Order, OrderItem
from pharmacies.models import Medicine, Pharmacy, Supplier
from purchases.models import Purchase


class FullFlowTest(APITestCase):
    """Полный сценарий: админ -> аптека -> директор -> фармацевт -> закупка -> продажа."""

    def setUp(self):
        User.objects.create_superuser("admin", password="admin12345")

    def login(self, username, password):
        response = self.client.post("/api/auth/login/", {"username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.data)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        return response

    def test_full_flow(self):
        # 1. Админ проекта создаёт аптеку и директора
        response = self.login("admin", "admin12345")
        self.assertEqual(response.data["user"]["role"], "admin")

        response = self.client.post("/api/pharmacies/", {"name": "Apteka 1", "address": "Dushanbe"})
        self.assertEqual(response.status_code, 201, response.data)
        pharmacy_id = response.data["id"]

        response = self.client.post(
            "/api/users/",
            {"username": "director", "password": "pass12345", "role": "director", "pharmacy": pharmacy_id},
        )
        self.assertEqual(response.status_code, 201, response.data)

        # директору без аптеки — ошибка
        response = self.client.post(
            "/api/users/", {"username": "bad", "password": "pass12345", "role": "director"}
        )
        self.assertEqual(response.status_code, 400)

        # 2. Директор: аптеку создать не может, фармацевта — может
        self.login("director", "pass12345")
        self.assertEqual(self.client.post("/api/pharmacies/", {"name": "X"}).status_code, 403)

        response = self.client.post(
            "/api/users/", {"username": "seller", "password": "pass12345", "role": "director"}
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["role"], "pharmacist")  # роль принудительно фармацевт
        self.assertEqual(response.data["pharmacy"], pharmacy_id)

        response = self.client.post(
            "/api/medicines/", {"title": "Paracetamol", "price": "10.00", "barcode": " 4601234567890 "}
        )
        self.assertEqual(response.status_code, 201, response.data)
        medicine_id = response.data["id"]
        self.assertEqual(response.data["quantity"], 0)
        self.assertEqual(response.data["barcode"], "4601234567890")

        # поиск по штрих-коду: точный (сканер) и через общий поиск; повтор кода — ошибка
        response = self.client.get("/api/medicines/", {"barcode": "4601234567890"})
        self.assertEqual([m["id"] for m in response.data["results"]], [medicine_id])
        self.assertEqual(self.client.get("/api/medicines/", {"barcode": "4601234567"}).data["count"], 0)
        self.assertEqual(self.client.get("/api/medicines/", {"search": "460123"}).data["count"], 1)
        response = self.client.post("/api/medicines/", {"title": "Other", "price": "1", "barcode": "4601234567890"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("barcode", response.data)

        response = self.client.post("/api/suppliers/", {"name": "Farm Supply"})
        self.assertEqual(response.status_code, 201, response.data)
        supplier_id = response.data["id"]

        response = self.client.post(
            "/api/purchases/",
            {
                "supplier": supplier_id,
                "items": [{"medicine": medicine_id, "quantity": 50, "price": "6.00", "expire_date": "2030-12-01"}],
            },
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["total_price"], "300.00")
        self.assertEqual(Medicine.objects.get(pk=medicine_id).quantity, 50)

        # 3. Фармацевт продаёт
        self.login("seller", "pass12345")
        self.assertEqual(self.client.post("/api/pharmacies/", {"name": "Y"}).status_code, 403)
        self.assertEqual(self.client.post("/api/medicines/", {"title": "Z", "price": "1"}).status_code, 403)
        self.assertEqual(self.client.get("/api/purchases/").status_code, 403)
        self.assertEqual(self.client.get("/api/users/").status_code, 403)

        response = self.client.post(
            "/api/orders/", {"discount": "10", "items": [{"medicine": medicine_id, "quantity": 5}]}
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["total_price"], "45.00")  # 5 * 10 - 10%
        order_id = response.data["id"]
        self.assertEqual(Medicine.objects.get(pk=medicine_id).quantity, 45)

        # больше, чем на складе — ошибка, остаток не меняется
        response = self.client.post(
            "/api/orders/", {"items": [{"medicine": medicine_id, "quantity": 100}]}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Medicine.objects.get(pk=medicine_id).quantity, 45)

        # фармацевт не может отменить заказ
        self.assertEqual(self.client.post(f"/api/orders/{order_id}/cancel/").status_code, 403)

        # 4. Директор отменяет заказ — товар возвращается
        self.login("director", "pass12345")

        # итоги месяца: продажи 45, себестоимость 5 × 6 = 30, прибыль 15, на закупки потрачено 300
        month = self.client.get(f"/api/pharmacies/{pharmacy_id}/stats/").data["monthly"][-1]
        self.assertEqual(
            (month["sales"], month["expenses"], month["profit"], month["purchases"]), (45, 30, 15, 300)
        )

        response = self.client.post(f"/api/orders/{order_id}/cancel/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "cancelled")
        self.assertEqual(Medicine.objects.get(pk=medicine_id).quantity, 50)

        response = self.client.get(f"/api/pharmacies/{pharmacy_id}/stats/")
        self.assertEqual(response.status_code, 200, response.data)
        # график: 14 дней подряд, отменённая продажа не учитывается ни по дням, ни в рейтинге
        self.assertEqual(len(response.data["sales_by_day"]), 14)
        self.assertTrue(all(day["total"] == 0 for day in response.data["sales_by_day"]))
        # лучших нет (единственная продажа отменена), лекарство без продаж — среди худших
        ranking = response.data["medicine_ranking"]
        self.assertEqual(ranking["best"], [])
        self.assertEqual([(m["medicine__title"], m["sold"]) for m in ranking["worst"]], [("Paracetamol", 0)])

        # лекарство из закупки удалить нельзя — понятная ошибка 400
        self.assertEqual(self.client.delete(f"/api/medicines/{medicine_id}/").status_code, 400)


class PurchaseSalePriceTest(APITestCase):
    """При закупке можно сразу задать новую цену продажи лекарства."""

    def setUp(self):
        pharmacy = Pharmacy.objects.create(name="A")
        User.objects.create_user("director", password="pass12345", role=User.Role.DIRECTOR, pharmacy=pharmacy)
        self.medicine = Medicine.objects.create(pharmacy=pharmacy, title="Paracetamol", price="10.00")
        self.supplier = Supplier.objects.create(pharmacy=pharmacy, name="Farm")
        response = self.client.post("/api/auth/login/", {"username": "director", "password": "pass12345"})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")

    def purchase(self, **extra):
        item = {"medicine": self.medicine.id, "quantity": 10, "price": "6.00", "expire_date": "2030-12-01", **extra}
        return self.client.post("/api/purchases/", {"supplier": self.supplier.id, "items": [item]})

    def test_sale_price(self):
        # без sale_price цена не меняется
        self.assertEqual(self.purchase().status_code, 201)
        self.medicine.refresh_from_db()
        self.assertEqual((self.medicine.price, self.medicine.quantity), (Decimal("10.00"), 10))

        # с sale_price — новая цена и приход на склад
        response = self.purchase(sale_price="12.50")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn("sale_price", response.data["items"][0])
        self.medicine.refresh_from_db()
        self.assertEqual((self.medicine.price, self.medicine.quantity), (Decimal("12.50"), 20))

        # нулевая цена — ошибка, ничего не меняется
        self.assertEqual(self.purchase(sale_price="0").status_code, 400)
        self.medicine.refresh_from_db()
        self.assertEqual((self.medicine.price, self.medicine.quantity), (Decimal("12.50"), 20))

class RBACTest(APITestCase):
    """Админ проекта создаёт сотрудников в аптеке; права приходят в /me."""

    def setUp(self):
        User.objects.create_superuser("admin", password="admin12345")
        self.pharmacy = Pharmacy.objects.create(name="A")
        self.other = Pharmacy.objects.create(name="B")

    def login(self, username, password):
        response = self.client.post("/api/auth/login/", {"username": username, "password": password})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        return response

    def test_admin_creates_users_in_pharmacy(self):
        response = self.login("admin", "admin12345")
        self.assertIn("user.manage", response.data["user"]["permissions"])

        url = f"/api/pharmacies/{self.pharmacy.id}/users/"
        response = self.client.post(
            url, {"username": "dir", "password": "pass12345", "role": "director"}
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["pharmacy"], self.pharmacy.id)

        # роль по умолчанию — фармацевт; аптека берётся из URL, а не из тела
        response = self.client.post(
            url, {"username": "ph", "password": "pass12345", "pharmacy": self.other.id}
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["role"], "pharmacist")
        self.assertEqual(response.data["pharmacy"], self.pharmacy.id)

        # админа внутри аптеки создать нельзя
        response = self.client.post(url, {"username": "x", "password": "pass12345", "role": "admin"})
        self.assertEqual(response.status_code, 400)

        self.assertEqual(self.client.get(url).data["count"], 2)

        # директор этим эндпоинтом пользоваться не может
        self.login("dir", "pass12345")
        response = self.client.post(url, {"username": "y", "password": "pass12345"})
        self.assertEqual(response.status_code, 403)

        response = self.client.get("/api/auth/me/")
        self.assertIn("user.manage_pharmacist", response.data["permissions"])
        self.assertNotIn("user.manage", response.data["permissions"])

    def test_staff_without_pharmacy_has_no_permissions(self):
        User.objects.create_user("lost", password="pass12345", role="director")
        self.login("lost", "pass12345")
        self.assertEqual(self.client.get("/api/medicines/").status_code, 403)
        self.assertEqual(self.client.get("/api/auth/me/").data["permissions"], [])


class IsolationTest(APITestCase):
    """Директор одной аптеки не видит данные другой."""

    def test_pharmacies_are_isolated(self):
        p1 = Pharmacy.objects.create(name="A")
        p2 = Pharmacy.objects.create(name="B")
        User.objects.create_user("d1", password="pass12345", role="director", pharmacy=p1)
        other = Medicine.objects.create(pharmacy=p2, title="Aspirin", price=5)

        response = self.client.post("/api/auth/login/", {"username": "d1", "password": "pass12345"})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")

        self.assertEqual(self.client.get(f"/api/medicines/{other.id}/").status_code, 404)
        self.assertEqual(self.client.get("/api/medicines/").data["count"], 0)
        self.assertEqual(self.client.get("/api/pharmacies/").data["count"], 1)
        self.assertEqual(self.client.get(f"/api/pharmacies/{p2.id}/").status_code, 404)

    def test_pharmacist_does_not_see_pharmacies(self):
        pharmacy = Pharmacy.objects.create(name="A")
        pharmacist = User.objects.create_user("ph1", password="pass12345", role="pharmacist", pharmacy=pharmacy)
        self.client.force_authenticate(pharmacist)
        self.assertEqual(self.client.get("/api/pharmacies/").status_code, 403)
        self.assertEqual(self.client.get(f"/api/pharmacies/{pharmacy.id}/").status_code, 403)
        self.assertNotIn("pharmacy.view", self.client.get("/api/auth/me/").data["permissions"])


class MonthFilterTest(APITestCase):
    """?month=ГГГГ-ММ в списках продаж и закупок; месяц считается в часовом поясе проекта."""

    def setUp(self):
        pharmacy = Pharmacy.objects.create(name="A")
        user = User.objects.create_user("director", password="pass12345", role=User.Role.DIRECTOR, pharmacy=pharmacy)
        supplier = Supplier.objects.create(pharmacy=pharmacy, name="Farm")
        tz = timezone.get_current_timezone()
        # 1 октября 00:30 по Душанбе — это ещё 30 сентября по UTC
        dates = [datetime(2026, 9, 15, 12, tzinfo=tz), datetime(2026, 10, 1, 0, 30, tzinfo=tz), datetime(2026, 10, 20, 9, tzinfo=tz)]
        for created in dates:
            order = Order.objects.create(pharmacy=pharmacy, user=user)
            purchase = Purchase.objects.create(pharmacy=pharmacy, supplier=supplier, user=user)
            Order.objects.filter(pk=order.pk).update(created_at=created)
            Purchase.objects.filter(pk=purchase.pk).update(created_at=created)
        self.client.force_authenticate(user)

    def test_month_filter(self):
        for url in ["/api/orders/", "/api/purchases/"]:
            count = lambda **params: self.client.get(url, params).data["count"]
            self.assertEqual(count(), 3, url)
            self.assertEqual(count(month="2026-10"), 2, url)
            self.assertEqual(count(month="2026-09"), 1, url)
            self.assertEqual(count(month="2026-08"), 0, url)
            for bad in ["2026-13", "10-2026", "abc"]:
                self.assertEqual(self.client.get(url, {"month": bad}).status_code, 400, (url, bad))

            # период «с … по …» включительно; граница 1 октября 00:30 по Душанбе — уже октябрь
            self.assertEqual(count(month_from="2026-09", month_to="2026-10"), 3, url)
            self.assertEqual(count(month_from="2026-10"), 2, url)
            self.assertEqual(count(month_to="2026-09"), 1, url)
            self.assertEqual(count(month_from="2025-11", month_to="2026-12"), 3, url)  # декабрь → до 1 января
            for params in [{"month_from": "2026-10", "month_to": "2026-09"}, {"month_to": "2026-00"}]:
                self.assertEqual(self.client.get(url, params).status_code, 400, (url, params))


class MedicineRankingTest(APITestCase):
    """Лучшие и худшие по продажам лекарства — в статистике аптеки и в личном отчёте."""

    def setUp(self):
        pharmacy = Pharmacy.objects.create(name="A")
        self.director = User.objects.create_user("director", password="pass12345", role=User.Role.DIRECTOR, pharmacy=pharmacy)
        self.seller = User.objects.create_user("seller", password="pass12345", role=User.Role.PHARMACIST, pharmacy=pharmacy)
        self.pharmacy_id = pharmacy.id
        sold = {"A": 9, "B": 5, "C": 5, "D": 1, "E": 0, "F": 0}  # B и C — поровну, E и F не продавались
        for title, qty in sold.items():
            medicine = Medicine.objects.create(pharmacy=pharmacy, title=title, price="10.00", quantity=50)
            if qty:
                order = Order.objects.create(pharmacy=pharmacy, user=self.seller, total_price=qty * 10)
                OrderItem.objects.create(order=order, medicine=medicine, quantity=qty, price="10.00")
        # продажа директора и отменённая продажа продавца — в рейтинг продавца не идут
        other = Order.objects.create(pharmacy=pharmacy, user=self.director, total_price=500)
        OrderItem.objects.create(order=other, medicine=Medicine.objects.get(title="F"), quantity=50, price="10.00")
        cancelled = Order.objects.create(pharmacy=pharmacy, user=self.seller, status=Order.Status.CANCELLED)
        OrderItem.objects.create(order=cancelled, medicine=Medicine.objects.get(title="E"), quantity=99, price="10.00")

    def ranking(self, user, url):
        self.client.force_authenticate(user)
        ranking = self.client.get(url).data["medicine_ranking"]
        pick = lambda rows: [(r["medicine__title"], r["sold"]) for r in rows]
        return pick(ranking["best"]), pick(ranking["worst"])

    def test_pharmacy_stats(self):
        best, worst = self.ranking(self.director, f"/api/pharmacies/{self.pharmacy_id}/stats/")
        self.assertEqual(best, [("F", 50), ("A", 9), ("B", 5), ("C", 5), ("D", 1)])
        self.assertEqual(worst, [("E", 0)])  # в двух списках одно лекарство не бывает

    def test_my_report(self):
        best, worst = self.ranking(self.seller, "/api/auth/me/report/")
        self.assertEqual(best, [("A", 9), ("B", 5), ("C", 5), ("D", 1)])
        self.assertEqual(worst, [("E", 0), ("F", 0)])  # продажа директора и отменённая — не мои


class WeekSummaryTest(APITestCase):
    """Неделя пн–вс: лучший и худший день по выручке, итог и прошлая неделя."""

    def setUp(self):
        pharmacy = Pharmacy.objects.create(name="A")
        self.director = User.objects.create_user("director", password="pass12345", role=User.Role.DIRECTOR, pharmacy=pharmacy)
        self.url = f"/api/pharmacies/{pharmacy.id}/week/"
        tz = timezone.get_current_timezone()

        def order(day, total, status=Order.Status.COMPLETED):
            o = Order.objects.create(pharmacy=pharmacy, user=self.director, total_price=total, status=status)
            Order.objects.filter(pk=o.pk).update(created_at=datetime(2025, 3, day, 12, tzinfo=tz))

        # неделя 3–9 марта 2025: пн 100, вт 300, ср 50 (+ отменённая 1000), пт 300; чт, сб, вс — без продаж
        for day, total in [(3, 100), (4, 300), (5, 50), (7, 300)]:
            order(day, total)
        order(5, 1000, status=Order.Status.CANCELLED)
        order(1, 80)  # суббота прошлой недели

    def test_week(self):
        self.client.force_authenticate(self.director)
        week = self.client.get(self.url, {"date": "2025-03-06"}).data  # любой день недели
        self.assertEqual((str(week["start"]), str(week["end"])), ("2025-03-03", "2025-03-09"))
        self.assertEqual([d["total"] for d in week["days"]], [100, 300, 50, 0, 300, 0, 0])
        self.assertEqual(str(week["best"]["date"]), "2025-03-04")  # 300 во вт и пт — берётся более ранний
        self.assertEqual(str(week["worst"]["date"]), "2025-03-06")  # 0 — самый ранний из пустых дней
        self.assertEqual((week["total"], week["count"], week["prev_total"]), (750, 4, 80))
        self.assertTrue(week["has_next"])

    def test_current_week_and_errors(self):
        self.client.force_authenticate(self.director)
        week = self.client.get(self.url).data  # по умолчанию — текущая неделя
        today = timezone.localdate()
        self.assertEqual([d["future"] for d in week["days"]], [d["date"] > today for d in week["days"]])
        self.assertFalse(week["has_next"])
        self.assertIsNone(week["best"])  # продаж на этой неделе нет
        self.assertEqual(self.client.get(self.url, {"date": "06.03.2025"}).status_code, 400)


class BlisterSaleTest(APITestCase):
    """Продажа по блистерам: упаковка №30 = 3 блистера; вскрытая упаковка, цена блистера, отмена, отчёты в упаковках."""

    def setUp(self):
        self.pharmacy = Pharmacy.objects.create(name="Блистерная")
        self.director = User.objects.create_user("bd", password="x", pharmacy=self.pharmacy, role=User.Role.DIRECTOR)
        self.med = Medicine.objects.create(pharmacy=self.pharmacy, title="Парацетамол №30", price=Decimal("30"), quantity=2, blisters=3)
        self.whole = Medicine.objects.create(pharmacy=self.pharmacy, title="Сироп", price=Decimal("20"), quantity=5)
        self.client.force_authenticate(self.director)

    def sell(self, *items):
        return self.client.post(
            "/api/orders/", {"items": [{"medicine": m.id, "unit": u, "quantity": q} for m, u, q in items]}, format="json"
        )

    def test_sell_blisters_opens_pack(self):
        r = self.sell((self.med, "blister", 1))
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["total_price"], "10.00")  # 30 / 3
        self.assertEqual(r.data["items"][0]["packs"], "0.333")
        self.med.refresh_from_db()
        self.assertEqual((self.med.quantity, self.med.open_blisters), (1, 2))  # одну вскрыли, 2 блистера осталось

        self.assertEqual(self.sell((self.med, "blister", 4)).status_code, 201)  # 2 из вскрытой + вскрыть ещё одну
        self.med.refresh_from_db()
        self.assertEqual((self.med.quantity, self.med.open_blisters), (0, 1))
        self.assertEqual(self.sell((self.med, "blister", 2)).status_code, 400)  # остался 1 блистер
        self.assertEqual(self.sell((self.med, "pack", 1)).status_code, 400)  # целых упаковок нет

        # список лекарств видит остаток в блистерах и цену блистера
        m = next(x for x in self.client.get("/api/medicines/").data["results"] if x["id"] == self.med.id)
        self.assertEqual((m["quantity"], m["open_blisters"], m["blisters"], m["blister_price"]), (0, 1, 3, "10.00"))

    def test_cancel_returns_blisters_and_reassembles_pack(self):
        order = self.sell((self.med, "blister", 3)).data  # ровно одна упаковка
        self.med.refresh_from_db()
        self.assertEqual((self.med.quantity, self.med.open_blisters), (1, 0))
        self.assertEqual(self.client.post(f"/api/orders/{order['id']}/cancel/").status_code, 200)
        self.med.refresh_from_db()
        self.assertEqual((self.med.quantity, self.med.open_blisters), (2, 0))  # блистеры снова собрались в упаковку

    def test_pack_only_medicine_and_reports_in_packs(self):
        self.assertEqual(self.sell((self.whole, "blister", 1)).status_code, 400)  # не делится
        self.assertEqual(self.sell((self.med, "blister", 2), (self.med, "pack", 1)).status_code, 201)
        report = self.client.get("/api/reports/sales/", {"section": "medicines"}).data
        row = report["sections"][0]["rows"][0]
        self.assertEqual(row[1], 1.67)  # 2/3 + 1 упаковка, до двух знаков

    def test_blisters_locked_while_pack_is_open(self):
        self.sell((self.med, "blister", 1))
        r = self.client.patch(f"/api/medicines/{self.med.id}/", {"blisters": 2}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("blisters", r.data)

    def test_update_returns_old_items_and_sells_new(self):
        order = self.sell((self.med, "pack", 2)).data  # продали всё
        url = f"/api/orders/{order['id']}/"
        items = [{"medicine": self.med.id, "unit": "blister", "quantity": 2}, {"medicine": self.whole.id, "quantity": 1}]
        r = self.client.put(url, {"discount": "50", "items": items}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["total_price"], "20.00")  # (2 × 10 + 20) − 50%
        self.med.refresh_from_db()
        self.whole.refresh_from_db()
        self.assertEqual((self.med.quantity, self.med.open_blisters, self.whole.quantity), (1, 1, 4))

        # не хватает — продажа и склад не меняются
        r = self.client.put(url, {"items": [{"medicine": self.whole.id, "quantity": 99}]}, format="json")
        self.assertEqual(r.status_code, 400)
        self.whole.refresh_from_db()
        self.assertEqual((self.whole.quantity, Order.objects.get(pk=order["id"]).items.count()), (4, 2))

        # фармацевту нельзя, отменённую — нельзя
        seller = User.objects.create_user("bs", password="x", pharmacy=self.pharmacy, role=User.Role.PHARMACIST)
        self.client.force_authenticate(seller)
        self.assertEqual(self.client.put(url, {"items": items}, format="json").status_code, 403)
        self.client.force_authenticate(self.director)
        self.client.post(f"/api/orders/{order['id']}/cancel/")
        self.assertEqual(self.client.put(url, {"items": items}, format="json").status_code, 400)
