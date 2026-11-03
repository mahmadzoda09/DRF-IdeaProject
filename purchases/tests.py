from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from pharmacies.models import Medicine, Pharmacy, Supplier
from purchases.models import Purchase, PurchaseItem


class PurchaseEditTest(APITestCase):
    """Изменение и удаление закупки: остатки пересчитываются, уже проданное убрать нельзя."""

    def setUp(self):
        self.pharmacy = Pharmacy.objects.create(name="Моя")
        self.director = User.objects.create_user("dir", password="x", pharmacy=self.pharmacy, role=User.Role.DIRECTOR)
        self.pharmacist = User.objects.create_user("ph", password="x", pharmacy=self.pharmacy, role=User.Role.PHARMACIST)
        self.supplier = Supplier.objects.create(pharmacy=self.pharmacy, name="S1")
        self.other_supplier = Supplier.objects.create(pharmacy=self.pharmacy, name="S2")
        self.a = Medicine.objects.create(pharmacy=self.pharmacy, title="A", price=10, quantity=0)
        self.b = Medicine.objects.create(pharmacy=self.pharmacy, title="B", price=20, quantity=0)
        self.expire = (timezone.localdate() + timedelta(days=300)).isoformat()
        self.client.force_authenticate(self.director)
        r = self.client.post("/api/purchases/", self.payload([(self.a, 10, "4.00")]), format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.purchase_id = r.data["id"]

    def payload(self, items, supplier=None):
        return {
            "supplier": (supplier or self.supplier).id,
            "items": [{"medicine": m.id, "quantity": q, "price": p, "expire_date": self.expire} for m, q, p in items],
        }

    def stock(self, medicine):
        medicine.refresh_from_db()
        return medicine.quantity

    def test_update_moves_stock_by_difference(self):
        r = self.client.put(
            f"/api/purchases/{self.purchase_id}/",
            self.payload([(self.a, 6, "4.00"), (self.b, 3, "5.00")], supplier=self.other_supplier),
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["total_price"], "39.00")
        self.assertEqual(r.data["supplier"], self.other_supplier.id)
        self.assertEqual(len(r.data["items"]), 2)
        self.assertEqual(self.stock(self.a), 6)
        self.assertEqual(self.stock(self.b), 3)

    def test_cannot_remove_already_sold(self):
        Medicine.objects.filter(pk=self.a.pk).update(quantity=3)  # 7 из 10 уже продано
        r = self.client.put(f"/api/purchases/{self.purchase_id}/", self.payload([(self.a, 5, "4.00")]), format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.delete(f"/api/purchases/{self.purchase_id}/").status_code, 400)
        self.assertEqual(self.stock(self.a), 3)
        self.assertEqual(PurchaseItem.objects.get(purchase_id=self.purchase_id).quantity, 10)

        # уменьшить на столько, сколько ещё лежит, — можно
        r = self.client.put(f"/api/purchases/{self.purchase_id}/", self.payload([(self.a, 7, "4.00")]), format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(self.stock(self.a), 0)

    def test_delete_removes_stock(self):
        self.assertEqual(self.client.delete(f"/api/purchases/{self.purchase_id}/").status_code, 204)
        self.assertFalse(Purchase.objects.filter(pk=self.purchase_id).exists())
        self.assertEqual(self.stock(self.a), 0)

    def test_written_off_purchase_is_locked(self):
        PurchaseItem.objects.filter(purchase_id=self.purchase_id).update(written_off=2)
        self.assertEqual(self.client.delete(f"/api/purchases/{self.purchase_id}/").status_code, 400)
        r = self.client.put(f"/api/purchases/{self.purchase_id}/", self.payload([(self.a, 10, "4.00")]), format="json")
        self.assertEqual(r.status_code, 400)

    def test_pharmacist_cannot_edit_or_delete(self):
        self.client.force_authenticate(self.pharmacist)
        self.assertEqual(self.client.delete(f"/api/purchases/{self.purchase_id}/").status_code, 403)
        r = self.client.put(f"/api/purchases/{self.purchase_id}/", self.payload([(self.a, 1, "4.00")]), format="json")
        self.assertEqual(r.status_code, 403)
