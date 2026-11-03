from datetime import date, datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from orders.models import Order, OrderItem
from pharmacies.models import Medicine, Pharmacy, Supplier
from purchases.models import Purchase, PurchaseItem


def at(year, month, day):
    return timezone.make_aware(datetime(year, month, day, 12))


class ReportsTest(TestCase):
    def setUp(self):
        self.pharmacy = Pharmacy.objects.create(name="Моя")
        other = Pharmacy.objects.create(name="Чужая")
        self.director = User.objects.create_user("dir", password="x", pharmacy=self.pharmacy, role=User.Role.DIRECTOR)
        self.pharmacist = User.objects.create_user("ph", password="x", pharmacy=self.pharmacy, role=User.Role.PHARMACIST)
        self.admin = User.objects.create_user("adm", password="x", role=User.Role.ADMIN)
        supplier = Supplier.objects.create(pharmacy=self.pharmacy, name="Шифо")
        med = Medicine.objects.create(pharmacy=self.pharmacy, title="Парацетамол", price=10, quantity=100)

        purchase = Purchase.objects.create(pharmacy=self.pharmacy, supplier=supplier, user=self.director, total_price=60)
        PurchaseItem.objects.create(purchase=purchase, medicine=med, quantity=10, price=6, expire_date=date(2027, 1, 1))
        Purchase.objects.filter(pk=purchase.pk).update(created_at=at(2026, 9, 5))

        # сентябрь: чек директора 2×10; октябрь: чек фармацевта 3×10, отменённый чек; чужая аптека — не видна
        for user, month, qty, state in [
            (self.director, 9, 2, Order.Status.COMPLETED),
            (self.pharmacist, 10, 3, Order.Status.COMPLETED),
            (self.pharmacist, 10, 1, Order.Status.CANCELLED),
        ]:
            order = Order.objects.create(pharmacy=self.pharmacy, user=user, total_price=qty * 10, status=state)
            OrderItem.objects.create(order=order, medicine=med, quantity=qty, price=10)
            Order.objects.filter(pk=order.pk).update(created_at=at(2026, month, 10))
        other_med = Medicine.objects.create(pharmacy=other, title="Секретин", price=5, quantity=5)
        foreign = Order.objects.create(pharmacy=other, total_price=5)
        OrderItem.objects.create(order=foreign, medicine=other_med, quantity=1, price=5)

        self.client = APIClient()

    def get(self, user, kind, **params):
        self.client.force_authenticate(user)
        return self.client.get(f"/api/reports/{kind}/", params, HTTP_ACCEPT_LANGUAGE="ru")

    @staticmethod
    def section(report, id_):
        return next(s for s in report["sections"] if s["id"] == id_)

    def test_director_sales_with_profit(self):
        r = self.get(self.director, "sales", month_from="2026-09", month_to="2026-10")
        self.assertEqual(r.status_code, 200)
        summary = {row[0]: row[1] for row in self.section(r.data, "summary")["rows"]}
        self.assertEqual(summary["Чеков"], 2)
        self.assertEqual(summary["Выручка, сомони"], Decimal("50.00"))
        self.assertEqual(summary["Себестоимость проданного, сомони"], Decimal("30.00"))  # 5 шт. × 6
        self.assertEqual(summary["Прибыль, сомони"], Decimal("20.00"))
        self.assertEqual(summary["Отменено чеков"], 1)
        months = self.section(r.data, "months")["rows"]
        self.assertEqual([m[0] for m in months], ["Сентябрь 2026", "Октябрь 2026"])
        self.assertNotIn("Секретин", str(r.data))
        self.assertEqual(len(self.section(r.data, "sellers")["rows"]), 2)

    def test_month_filter(self):
        r = self.get(self.director, "sales", month_from="2026-10", month_to="2026-10")
        self.assertEqual(len(self.section(r.data, "list")["rows"]), 1)
        self.assertIn(["Период", "Октябрь 2026"], r.data["meta"])

    def test_pharmacist_sees_only_own_sales_without_profit(self):
        r = self.get(self.pharmacist, "sales")
        summary = {row[0]: row[1] for row in self.section(r.data, "summary")["rows"]}
        self.assertEqual(summary["Чеков"], 1)
        self.assertNotIn("Прибыль, сомони", summary)
        self.assertNotIn("sellers", [s["id"] for s in r.data["sections"]])
        self.assertEqual(self.get(self.pharmacist, "purchases").status_code, 403)

    def test_purchases(self):
        r = self.get(self.director, "purchases")
        summary = {row[0]: row[1] for row in self.section(r.data, "summary")["rows"]}
        self.assertEqual(summary["Закупок"], 1)
        self.assertEqual(summary["Потрачено, сомони"], Decimal("60.00"))
        self.assertEqual(self.section(r.data, "suppliers")["rows"], [["Шифо", 1, Decimal("60.00")]])

    def test_admin_all_or_one_pharmacy(self):
        all_list = self.section(self.get(self.admin, "sales").data, "list")["rows"]
        self.assertEqual(len(all_list), 3)
        one = self.get(self.admin, "sales", pharmacy=self.pharmacy.id).data
        self.assertEqual(len(self.section(one, "list")["rows"]), 2)
        self.assertIn(["Аптека", "Моя"], one["meta"])

    def test_exports(self):
        signatures = {"xlsx": b"PK", "docx": b"PK", "pdf": b"%PDF"}
        for export, magic in signatures.items():
            for kind in ("sales", "purchases"):
                r = self.get(self.director, kind, export=export, month_from="2026-09", month_to="2026-10")
                self.assertEqual(r.status_code, 200, (kind, export))
                self.assertTrue(r.content.startswith(magic), (kind, export))
                self.assertIn(f"{kind}_report_2026-09_2026-10_", r["Content-Disposition"])
        self.assertEqual(self.get(self.director, "sales", export="csv").status_code, 400)

    def test_single_section(self):
        r = self.get(self.director, "purchases", section="suppliers")
        self.assertEqual([s["id"] for s in r.data["sections"]], ["suppliers"])
        self.assertEqual(r.data["title"], "Отчёт о закупках. По поставщикам")
        self.assertEqual(self.get(self.director, "sales", section="nope").status_code, 400)
        # у фармацевта нет раздела «по продавцам»
        self.assertEqual(self.get(self.pharmacist, "sales", section="sellers").status_code, 400)
        for export in ("xlsx", "docx", "pdf"):
            r = self.get(self.director, "sales", section="months", export=export, month_from="2026-09", month_to="2026-10")
            self.assertEqual(r.status_code, 200, export)
            self.assertIn("sales_months_report_2026-09_2026-10_", r["Content-Disposition"])

    def test_single_section_xlsx_has_table_on_first_sheet(self):
        import io

        import openpyxl

        r = self.get(self.director, "sales", section="medicines", export="xlsx")
        ws = openpyxl.load_workbook(io.BytesIO(r.content)).active
        values = [[c.value for c in row] for row in ws.iter_rows()]
        self.assertEqual(values[0][0], "Отчёт о продажах. По лекарствам")
        self.assertIn(["Парацетамол", 5, 50.0, 30.0, 20.0], values)

    def test_signature_names_pharmacy_director(self):
        self.director.full_name = "Махмадов Абдуллочон"
        self.director.save()
        sign = self.get(self.pharmacist, "sales").data["signature"]
        self.assertEqual(sign["role"], "Директор аптеки")
        self.assertEqual(sign["name"], "Махмадов Абдуллочон")
        # админ по всем аптекам — директора нет, остаётся пустая строка для Ф. И. О.
        self.assertEqual(self.get(self.admin, "sales").data["signature"]["name"], "")

    def test_sign_date_is_end_of_last_month(self):
        def sign_date(**params):
            return self.get(self.director, "sales", **params).data["signature"]["date_value"]

        self.assertEqual(sign_date(month_from="2026-10", month_to="2026-10"), "31.10.2026")
        self.assertEqual(sign_date(month_from="2026-08", month_to="2026-09"), "30.09.2026")
        self.assertEqual(sign_date(month_from="2028-02", month_to="2028-02"), "29.02.2028")  # високосный
        self.assertEqual(sign_date(month_from="2026-12", month_to="2026-12"), "31.12.2026")
        self.assertEqual(sign_date(), "")  # за всё время — дата от руки
