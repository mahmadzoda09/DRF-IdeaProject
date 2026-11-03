import uuid

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class WriteOff(models.Model):
    """
    Списание просроченных лекарств со склада. Отдельный документ: остаток уменьшается, как при продаже,
    но в выручку не попадает. total_cost — потери по закупочной цене.
    """

    write_off_number = models.CharField(max_length=50, unique=True, editable=False)
    pharmacy = models.ForeignKey("pharmacies.Pharmacy", on_delete=models.CASCADE, related_name="write_offs")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="write_offs"
    )
    # месяц истечения срока (1-е число), за который списали; пусто — списали всё просроченное
    expire_month = models.DateField(null=True, blank=True)
    total_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def save(self, *args, **kwargs):
        if not self.write_off_number:
            self.write_off_number = f"WO-{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.write_off_number


class WriteOffItem(models.Model):
    write_off = models.ForeignKey(WriteOff, on_delete=models.CASCADE, related_name="items")
    medicine = models.ForeignKey("pharmacies.Medicine", on_delete=models.PROTECT)
    # партия (строка закупки), из которой списали
    purchase_item = models.ForeignKey("purchases.PurchaseItem", on_delete=models.PROTECT, related_name="write_off_items")
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    cost = models.DecimalField(max_digits=10, decimal_places=2)  # закупочная цена партии
    price = models.DecimalField(max_digits=10, decimal_places=2)  # цена продажи на момент списания
    expire_date = models.DateField()

    def __str__(self):
        return f"{self.medicine} x {self.quantity}"
