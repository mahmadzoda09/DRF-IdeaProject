from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from orders.models import Order
from pharmacies.models import Medicine, Pharmacy, Supplier
from purchases.models import Purchase, PurchaseItem


class WriteOffTest(APITestCase):
    """Списание просроченного по месяцам срока: остаток уменьшается, партия помечается, продажи не трогаются."""

    def setUp(self):
        self.today = timezone.localdate()
        self.pharmacy = Pharmacy.objects.create(name="Моя")
        other = Pharmacy.objects.create(name="Чужая")
        self.director = User.objects.create_user("dir", password="x", pharmacy=self.pharmacy, role=User.Role.DIRECTOR)
        self.pharmacist = User.objects.create_user("ph", password="x", pharmacy=self.pharmacy, role=User.Role.PHARMACIST)
        self.admin = User.objects.create_user("adm", password="x", role=User.Role.ADMIN)

        # два месяца назад и месяц назад — разные месяцы срока
        self.old = (self.today.replace(day=1) - timedelta(days=40)).replace(day=10)
        self.recent = (self.today.replace(day=1) - timedelta(days=1)).replace(day=5)

        def med(pharmacy, title, batches, quantity=None):
            m = Medicine.objects.create(pharmacy=pharmacy, title=title, price=10, quantity=0)
            for n, (qty, expire) in enumerate(batches):
                p = Purchase.objects.create(pharmacy=pharmacy, supplier=Supplier.objects.get_or_create(pharmacy=pharmacy, name="S")[0])
                Purchase.objects.filter(pk=p.pk).update(created_at=timezone.now() - timedelta(days=100 - n))
                PurchaseItem.objects.create(purchase=p, medicine=m, quantity=qty, price=4, expire_date=expire)
                m.quantity += qty
            m.quantity = m.quantity if quantity is None else quantity
            m.save()
            return m

        # новая партия просрочена раньше старой: FIFO должен списать именно её, а не старую
        self.mixed = med(self.pharmacy, "Смешанный", [(10, self.today + timedelta(days=200)), (5, self.recent)])
        self.old_med = med(self.pharmacy, "Старый", [(3, self.old)])
        self.fresh = med(self.pharmacy, "Свежий", [(8, self.today + timedelta(days=100))])
        self.foreign = med(other, "Чужой просроченный", [(4, self.old)])
        self.client.force_authenticate(self.director)

    def test_pending_by_month(self):
        r = self.client.get("/api/write-offs/pending/")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual([m["month"] for m in r.data["months"]], [self.old.strftime("%Y-%m"), self.recent.strftime("%Y-%m")])
        self.assertEqual(r.data["total"]["quantity"], 8)  # 3 + 5, чужая аптека не видна
        self.assertEqual(r.data["total"]["cost"], "32.00")

    def test_write_off_one_month(self):
        r = self.client.post("/api/write-offs/", {"month": self.recent.strftime("%Y-%m")}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual([(i["medicine_title"], i["quantity"]) for i in r.data["items"]], [("Смешанный", 5)])
        self.assertEqual(r.data["total_cost"], "20.00")
        self.mixed.refresh_from_db()
        self.assertEqual(self.mixed.quantity, 10)
        # на складе осталась свежая партия — срок лекарства теперь её
        meds = {m["title"]: m for m in self.client.get("/api/medicines/").data["results"]}
        self.assertEqual(meds["Смешанный"]["expire_status"], "ok")
        self.assertEqual(meds["Старый"]["expire_status"], "expired")
        # второй месяц ещё в списке, этот — уже нет
        months = [m["month"] for m in self.client.get("/api/write-offs/pending/").data["months"]]
        self.assertEqual(months, [self.old.strftime("%Y-%m")])

    def test_write_off_all_and_nothing_left(self):
        r = self.client.post("/api/write-offs/", {}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(sorted(i["medicine_title"] for i in r.data["items"]), ["Смешанный", "Старый"])
        self.assertEqual(self.client.post("/api/write-offs/", {}, format="json").status_code, 400)
        self.assertEqual(self.client.get("/api/write-offs/pending/").data["months"], [])
        self.assertEqual(Order.objects.count(), 0)  # в продажи не попадает
        self.foreign.refresh_from_db()
        self.assertEqual(self.foreign.quantity, 4)
        self.assertEqual(len(self.client.get("/api/write-offs/").data["results"]), 1)

    def test_permissions_and_validation(self):
        self.assertEqual(self.client.post("/api/write-offs/", {"month": "2026-13"}, format="json").status_code, 400)
        self.client.force_authenticate(self.pharmacist)  # списывает только директор
        self.assertEqual(self.client.get("/api/write-offs/pending/").status_code, 403)
        self.assertEqual(self.client.post("/api/write-offs/", {}, format="json").status_code, 403)
        self.client.force_authenticate(self.admin)
        r = self.client.get("/api/write-offs/pending/")
        self.assertEqual(r.data["total"]["quantity"], 12)  # админ видит все аптеки
        self.assertEqual(self.client.post("/api/write-offs/", {}, format="json").status_code, 403)
