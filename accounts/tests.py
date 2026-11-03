from datetime import datetime

from django.utils import timezone
from rest_framework.test import APITestCase

from orders.models import Order, OrderItem
from pharmacies.models import Medicine, Pharmacy, Supplier
from purchases.models import Purchase, PurchaseItem

from .models import User


class MyReportTest(APITestCase):
    """/api/auth/me/report/: каждый видит только свои продажи (и свои закупки, если их оформляет)."""

    def setUp(self):
        pharmacy = Pharmacy.objects.create(name="A")
        self.director = User.objects.create_user("director", password="pass12345", role=User.Role.DIRECTOR, pharmacy=pharmacy)
        self.seller = User.objects.create_user("seller", password="pass12345", role=User.Role.PHARMACIST, pharmacy=pharmacy)
        medicine = Medicine.objects.create(pharmacy=pharmacy, title="Нурофен", price="35.00")
        supplier = Supplier.objects.create(pharmacy=pharmacy, name="Farm")
        tz = timezone.get_current_timezone()

        def order(user, total, qty, when, status=Order.Status.COMPLETED):
            o = Order.objects.create(pharmacy=pharmacy, user=user, total_price=total, status=status)
            OrderItem.objects.create(order=o, medicine=medicine, quantity=qty, price="35.00")
            Order.objects.filter(pk=o.pk).update(created_at=when)

        order(self.seller, "70.00", 2, datetime(2026, 9, 10, 12, tzinfo=tz))
        order(self.seller, "35.00", 1, datetime(2026, 10, 5, 12, tzinfo=tz))
        order(self.seller, "35.00", 1, datetime(2026, 10, 6, 12, tzinfo=tz), status=Order.Status.CANCELLED)
        order(self.director, "350.00", 10, datetime(2026, 10, 7, 12, tzinfo=tz))  # чужая для продавца
        Purchase.objects.create(pharmacy=pharmacy, supplier=supplier, user=self.director, total_price="500.00")

    def report(self, user, **params):
        self.client.force_authenticate(user)
        response = self.client.get("/api/auth/me/report/", params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_only_own_sales(self):
        data = self.report(self.seller)
        self.assertEqual(
            (data["sales"]["count"], data["sales"]["total"], data["sales"]["items_sold"], data["sales"]["cancelled"]),
            (2, 105, 3, 1),
        )
        self.assertEqual(data["sales"]["avg_check"], 52.5)
        self.assertEqual(len(data["recent_orders"]), 3)  # вместе с отменённой
        self.assertIsNone(data["purchases"])  # фармацевт не закупает
        self.assertEqual(len(data["sales_by_day"]), 14)

        director = self.report(self.director)
        self.assertEqual((director["sales"]["count"], director["sales"]["total"]), (1, 350))
        self.assertEqual((director["purchases"]["count"], director["purchases"]["total"]), (1, 500))

    def test_period(self):
        october = self.report(self.seller, month_from="2026-10", month_to="2026-10")
        self.assertEqual((october["sales"]["count"], october["sales"]["total"], october["sales"]["cancelled"]), (1, 35, 1))
        september = self.report(self.seller, month="2026-09")
        self.assertEqual([m["sold"] for m in september["medicine_ranking"]["best"]], [2])

        self.client.force_authenticate(self.seller)
        bad = self.client.get("/api/auth/me/report/", {"month_from": "2026-10", "month_to": "2026-09"})
        self.assertEqual(bad.status_code, 400)

    def test_finance(self):
        # средняя закупочная цена 20 → продавец продал 3 уп.: расходы 60, продажи 105, прибыль 45
        PurchaseItem.objects.create(
            purchase=Purchase.objects.get(), medicine=Medicine.objects.get(), quantity=10, price="20.00", expire_date="2030-01-01"
        )
        finance = self.report(self.seller)["finance"]
        self.assertEqual((finance["sales"], finance["expenses"], finance["profit"]), (105, 60, 45))
        self.assertEqual(self.report(self.seller, month="2026-10")["finance"]["expenses"], 20)  # только октябрь

    def test_my_week(self):
        self.client.force_authenticate(self.seller)
        week = self.client.get("/api/auth/me/week/", {"date": "2026-10-07"}).data  # неделя 5–11 октября
        self.assertEqual((week["total"], week["count"]), (35, 1))  # 7-го — продажа директора, не моя

    def test_requires_login(self):
        self.assertEqual(self.client.get("/api/auth/me/report/").status_code, 401)


class AccessTest(APITestCase):
    """Директор даёт фармацевту закупки и отнимает кассу; права зависят друг от друга; чужих и директоров не трогает."""

    def setUp(self):
        self.pharmacy = Pharmacy.objects.create(name="Моя")
        other = Pharmacy.objects.create(name="Чужая")
        self.director = User.objects.create_user("dir", password="x", pharmacy=self.pharmacy, role=User.Role.DIRECTOR)
        self.ph = User.objects.create_user("ph", password="x", pharmacy=self.pharmacy, role=User.Role.PHARMACIST)
        self.foreign = User.objects.create_user("ph2", password="x", pharmacy=other, role=User.Role.PHARMACIST)
        self.supplier = Supplier.objects.create(pharmacy=self.pharmacy, name="Шифо")
        self.med = Medicine.objects.create(pharmacy=self.pharmacy, title="Аспирин", price=10)

    def access(self):
        data = self.client.get(f"/api/users/{self.ph.id}/access/").data
        return {i["perm"] for g in data["groups"] for i in g["items"] if i["granted"]}

    def test_grant_purchases_and_revoke_sales(self):
        self.client.force_authenticate(self.director)
        before = self.access()
        self.assertIn("order.create", before)
        self.assertNotIn("purchase.create", before)

        # дали «создавать закупки» — «смотреть закупки» добавилось само; кассу отняли
        wanted = (before - {"order.create"}) | {"purchase.create"}
        r = self.client.put(f"/api/users/{self.ph.id}/access/", {"permissions": sorted(wanted)}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        after = self.access()
        self.assertTrue({"purchase.create", "purchase.view"} <= after)
        self.assertNotIn("order.create", after)

        self.ph.refresh_from_db()  # в настоящих запросах пользователь читается из базы заново
        self.client.force_authenticate(self.ph)
        me = self.client.get("/api/auth/me/").data["permissions"]
        self.assertIn("purchase.create", me)
        purchase = {"supplier": self.supplier.id, "items": [{"medicine": self.med.id, "quantity": 5, "price": "6.00", "expire_date": "2030-01-01"}]}
        self.assertEqual(self.client.post("/api/purchases/", purchase, format="json").status_code, 201)
        sale = {"items": [{"medicine": self.med.id, "quantity": 1}]}
        self.assertEqual(self.client.post("/api/orders/", sale, format="json").status_code, 403)

    def test_rules(self):
        self.client.force_authenticate(self.director)
        self.assertEqual(self.client.get(f"/api/users/{self.foreign.id}/access/").status_code, 404)  # чужая аптека
        bad = self.client.put(f"/api/users/{self.ph.id}/access/", {"permissions": ["user.manage"]}, format="json")
        self.assertEqual(bad.status_code, 400)
        self.client.force_authenticate(self.ph)  # сам себе не даёт
        self.assertEqual(self.client.get(f"/api/users/{self.ph.id}/access/").status_code, 403)
        admin = User.objects.create_user("adm", password="x", role=User.Role.ADMIN)
        self.client.force_authenticate(admin)
        self.assertEqual(self.client.get(f"/api/users/{self.foreign.id}/access/").status_code, 200)
        self.assertEqual(self.client.get(f"/api/users/{self.director.id}/access/").status_code, 400)  # только фармацевтам



class AvatarTest(APITestCase):
    """Аватарка: любая картинка → квадрат 640×640 jpg; не картинка — отказ; админ ставит любому, директор — своим фармацевтам."""

    def setUp(self):
        import shutil
        import tempfile

        from django.test import override_settings

        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        from pharmacies.models import Pharmacy

        self.pharmacy = Pharmacy.objects.create(name="Моя")
        self.admin = User.objects.create_superuser("adm", password="x")
        self.director = User.objects.create_user("dir", password="x", pharmacy=self.pharmacy, role=User.Role.DIRECTOR)
        self.pharmacist = User.objects.create_user("ph", password="x", pharmacy=self.pharmacy, role=User.Role.PHARMACIST)

    def image(self, size=(800, 400), name="me.png", fmt="PNG"):
        from io import BytesIO

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        out = BytesIO()
        Image.new("RGB", size, (200, 30, 30)).save(out, fmt)
        return SimpleUploadedFile(name, out.getvalue(), content_type="image/png")

    def test_my_avatar(self):
        from PIL import Image

        self.client.force_authenticate(self.pharmacist)
        r = self.client.post("/api/auth/me/avatar/", {"file": self.image()}, format="multipart")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertRegex(r.data["avatar_url"], r"^/media/avatars/[0-9a-f]{32}\.jpg$")
        self.pharmacist.refresh_from_db()
        with Image.open(self.pharmacist.avatar.path) as saved:
            self.assertEqual((saved.size, saved.format), ((640, 640), "JPEG"))
        old = self.pharmacist.avatar.path
        self.client.post("/api/auth/me/avatar/", {"file": self.image(name="2.png")}, format="multipart")
        import os

        self.assertFalse(os.path.exists(old))  # старая удаляется с диска
        self.assertIsNone(self.client.delete("/api/auth/me/avatar/").data["avatar_url"])

        from django.core.files.uploadedfile import SimpleUploadedFile

        fake = SimpleUploadedFile("evil.png", b"<script>alert(1)</script>", content_type="image/png")
        self.assertEqual(self.client.post("/api/auth/me/avatar/", {"file": fake}, format="multipart").status_code, 400)

    def test_others_avatar(self):
        url = f"/api/users/{self.pharmacist.id}/avatar/"
        self.client.force_authenticate(self.director)  # директор — своему фармацевту
        self.assertEqual(self.client.post(url, {"file": self.image()}, format="multipart").status_code, 200)
        self.assertEqual(self.client.post(f"/api/users/{self.admin.id}/avatar/", {"file": self.image()}, format="multipart").status_code, 404)
        self.client.force_authenticate(self.pharmacist)
        self.assertEqual(self.client.post(url, {"file": self.image()}, format="multipart").status_code, 403)
        self.client.force_authenticate(self.admin)  # админ — любому
        r = self.client.post(f"/api/users/{self.director.id}/avatar/", {"file": self.image()}, format="multipart")
        self.assertTrue(r.data["avatar_url"])
        self.assertIsNone(self.client.delete(f"/api/users/{self.director.id}/avatar/").data["avatar_url"])
