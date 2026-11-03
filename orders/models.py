import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Order(models.Model):
    """Продажа лекарств покупателю."""

    class Status(models.TextChoices):
        COMPLETED = "completed", "Завершён"
        CANCELLED = "cancelled", "Отменён"

    order_number = models.CharField(max_length=50, unique=True, editable=False)
    pharmacy = models.ForeignKey("pharmacies.Pharmacy", on_delete=models.CASCADE, related_name="orders")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="orders"
    )
    # кому продали; не выбран при продаже — клиент аптеки по умолчанию («Саховат»)
    # скидка в процентах: 0 - 100
    discount = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    total_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.COMPLETED)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def save(self, *args, **kwargs):
        if not self.order_number:
            self.order_number = f"ORD-{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.order_number


class OrderItem(models.Model):
    class Unit(models.TextChoices):
        PACK = "pack", "Упаковка"
        BLISTER = "blister", "Блистер"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    medicine = models.ForeignKey("pharmacies.Medicine", on_delete=models.PROTECT)
    unit = models.CharField(max_length=10, choices=Unit.choices, default=Unit.PACK)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])  # в единицах unit
    # цена единицы на момент продажи (если цена лекарства потом изменится, заказ не поменяется)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    # то же количество в упаковках (2 блистера из 3 = 0.667) — для отчётов и себестоимости
    packs = models.DecimalField(max_digits=12, decimal_places=3, default=0)

    def save(self, *args, **kwargs):
        if not self.packs:
            blisters = self.medicine.blisters if self.unit == self.Unit.BLISTER else 1
            self.packs = (Decimal(self.quantity) / blisters).quantize(Decimal("0.001"))
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.medicine} x {self.quantity}"
