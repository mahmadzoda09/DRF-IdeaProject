from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Pharmacy(models.Model):
    name = models.CharField(max_length=255, unique=True)
    address = models.CharField(max_length=255, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    # фото (аватар) чата аптеки — jpg 640×640, ставят директор и админ
    chat_avatar = models.FileField(upload_to="chat-avatars/", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        verbose_name_plural = "pharmacies"

    def __str__(self):
        return self.name


class Supplier(models.Model):
    pharmacy = models.ForeignKey(Pharmacy, on_delete=models.CASCADE, related_name="suppliers")
    name = models.CharField(max_length=255)
    phone_number = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return self.name


IN_STOCK = models.Q(quantity__gt=0) | models.Q(open_blisters__gt=0)  # есть целые упаковки или блистеры вскрытой


class Medicine(models.Model):
    pharmacy = models.ForeignKey(Pharmacy, on_delete=models.CASCADE, related_name="medicines")
    title = models.CharField(max_length=255)
    # штрих-код упаковки (EAN-13 и т.п.); пустой — если не задан
    barcode = models.CharField(max_length=32, blank=True, db_index=True)
    price = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    # остаток в целых упаковках; меняется только через закупки (+) и продажи (-)
    quantity = models.PositiveIntegerField(default=0)
    # сколько блистеров (пластинок) в упаковке: №30 по 10 таблеток — 3. 1 — упаковку не делят
    blisters = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(100)])
    # блистеры вскрытой упаковки, ещё не проданные (меньше blisters); в quantity не входят
    open_blisters = models.PositiveIntegerField(default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(fields=["pharmacy", "title"], name="unique_medicine_per_pharmacy"),
            models.UniqueConstraint(
                fields=["pharmacy", "barcode"],
                condition=~models.Q(barcode=""),
                name="unique_barcode_per_pharmacy",
            ),
        ]

    def __str__(self):
        return self.title

    @property
    def blister_price(self):
        """Цена одного блистера: цена упаковки поровну на все блистеры."""
        return (self.price / self.blisters).quantize(Decimal("0.01"))

    @property
    def stock_packs(self):
        """Остаток для сроков годности: вскрытая упаковка считается целой (её блистеры из той же партии)."""
        return self.quantity + (1 if self.open_blisters else 0)

    def take(self, unit, quantity):
        """Убрать проданное со склада (без save). False — не хватает."""
        if unit == "pack":
            if self.quantity < quantity:
                return False
            self.quantity -= quantity
            return True
        if self.quantity * self.blisters + self.open_blisters < quantity:
            return False
        if self.open_blisters < quantity:  # вскрываем столько упаковок, сколько нужно
            opened = -(-(quantity - self.open_blisters) // self.blisters)
            self.quantity -= opened
            self.open_blisters += opened * self.blisters
        self.open_blisters -= quantity
        return True

    def give_back(self, unit, quantity):
        """Вернуть на склад (отмена продажи, без save): из блистеров снова собираются целые упаковки."""
        if unit == "pack":
            self.quantity += quantity
            return
        self.open_blisters += quantity
        self.quantity += self.open_blisters // self.blisters
        self.open_blisters %= self.blisters
