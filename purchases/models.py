import uuid

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class Purchase(models.Model):
    """Закупка лекарств у поставщика (приход на склад)."""

    purchase_number = models.CharField(max_length=50, unique=True, editable=False)
    supplier = models.ForeignKey("pharmacies.Supplier", on_delete=models.PROTECT, related_name="purchases")
    pharmacy = models.ForeignKey("pharmacies.Pharmacy", on_delete=models.CASCADE, related_name="purchases")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="purchases"
    )
    total_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def save(self, *args, **kwargs):
        if not self.purchase_number:
            self.purchase_number = f"PUR-{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.purchase_number


class PurchaseItem(models.Model):
    purchase = models.ForeignKey(Purchase, on_delete=models.CASCADE, related_name="items")
    medicine = models.ForeignKey("pharmacies.Medicine", on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    # закупочная цена (у поставщика)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    # срок годности этой партии
    expire_date = models.DateField()
    # сколько из партии списано (просрочка); на складе по этой партии может остаться не больше quantity - written_off
    written_off = models.PositiveIntegerField(default=0, editable=False)

    def __str__(self):
        return f"{self.medicine} x {self.quantity}"
