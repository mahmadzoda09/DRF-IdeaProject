from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from pharmacies.models import Medicine

from .models import Purchase, PurchaseItem


class PurchaseItemSerializer(serializers.ModelSerializer):
    medicine_title = serializers.CharField(source="medicine.title", read_only=True)
    # необязательно: новая цена продажи лекарства — применяется вместе с закупкой
    sale_price = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0.01"), required=False, write_only=True
    )

    class Meta:
        model = PurchaseItem
        fields = ["id", "medicine", "medicine_title", "quantity", "price", "sale_price", "expire_date"]

    def validate_expire_date(self, value):
        if value <= timezone.localdate():
            raise serializers.ValidationError("Срок годности уже истёк.")
        return value


class PurchaseSerializer(serializers.ModelSerializer):
    items = PurchaseItemSerializer(many=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)

    class Meta:
        model = Purchase
        fields = [
            "id",
            "purchase_number",
            "supplier",
            "supplier_name",
            "pharmacy",
            "user",
            "total_price",
            "created_at",
            "items",
        ]
        read_only_fields = ["purchase_number", "pharmacy", "user", "total_price", "created_at"]

    def validate_supplier(self, supplier):
        if supplier.pharmacy_id != self.context["request"].user.pharmacy_id:
            raise serializers.ValidationError("Поставщик не из вашей аптеки.")
        return supplier

    def validate_items(self, items):
        if not items:
            raise serializers.ValidationError("Добавьте хотя бы одно лекарство.")
        pharmacy_id = self.context["request"].user.pharmacy_id
        for item in items:
            if item["medicine"].pharmacy_id != pharmacy_id:
                raise serializers.ValidationError(
                    f"Лекарство id={item['medicine'].id} не из вашей аптеки."
                )
        return items

    def create(self, validated_data):
        items = validated_data.pop("items")

        with transaction.atomic():
            purchase = Purchase.objects.create(**validated_data)
            move_stock(stock_deltas(items))  # приход на склад
            self._save_items(purchase, items)

        return purchase

    def update(self, purchase, validated_data):
        """Изменить закупку: склад меняется на разницу между старыми и новыми позициями."""
        items = validated_data.pop("items", None)

        with transaction.atomic():
            check_editable(purchase)
            purchase.supplier = validated_data.get("supplier", purchase.supplier)
            purchase.save(update_fields=["supplier"])
            if items is not None:
                old = stock_deltas(purchase.items.all(), sign=-1)
                new = stock_deltas(items)
                move_stock({mid: new.get(mid, 0) + old.get(mid, 0) for mid in old.keys() | new.keys()})
                purchase.items.all().delete()
                self._save_items(purchase, items)

        return purchase

    def _save_items(self, purchase, items):
        """Создать строки закупки, применить новые цены продажи и посчитать итог."""
        total = Decimal("0")
        for item in items:
            sale_price = item.pop("sale_price", None)
            if sale_price is not None:
                Medicine.objects.filter(pk=item["medicine"].pk).exclude(price=sale_price).update(price=sale_price)
            PurchaseItem.objects.create(purchase=purchase, **item)
            total += item["price"] * item["quantity"]
        purchase.total_price = total
        purchase.save(update_fields=["total_price"])


def stock_deltas(items, sign=1):
    """Сколько упаковок каждого лекарства добавить на склад (sign=-1 — убрать)."""
    deltas = {}
    for item in items:
        medicine_id, quantity = (item["medicine"].pk, item["quantity"]) if isinstance(item, dict) else (item.medicine_id, item.quantity)
        deltas[medicine_id] = deltas.get(medicine_id, 0) + sign * quantity
    return deltas


def move_stock(deltas):
    """Изменить остатки; если товар закупки уже продан и убрать его нельзя — ошибка."""
    for medicine_id, delta in sorted(deltas.items()):
        if not delta:
            continue
        medicine = Medicine.objects.select_for_update().get(pk=medicine_id)
        if medicine.quantity + delta < 0:
            raise serializers.ValidationError(
                {"detail": f"«{medicine.title}» уже продан: на складе {medicine.quantity} уп., а убрать нужно {-delta}."}
            )
        medicine.quantity += delta
        medicine.save(update_fields=["quantity"])


def check_editable(purchase):
    if purchase.items.filter(written_off__gt=0).exists():
        raise serializers.ValidationError(
            {"detail": "Из этой закупки уже списан просроченный товар — изменить или удалить её нельзя."}
        )
