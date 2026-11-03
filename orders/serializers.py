from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from pharmacies.models import Medicine

from .models import Order, OrderItem


class OrderItemSerializer(serializers.ModelSerializer):
    medicine_title = serializers.CharField(source="medicine.title", read_only=True)

    class Meta:
        model = OrderItem
        fields = ["id", "medicine", "medicine_title", "unit", "quantity", "price", "packs"]
        read_only_fields = ["price", "packs"]


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True)
    seller = serializers.CharField(source="user.username", read_only=True, default=None)

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "pharmacy",
            "user",
            "seller",
            "discount",
            "total_price",
            "status",
            "created_at",
            "items",
        ]
        read_only_fields = ["order_number", "pharmacy", "user", "total_price", "status", "created_at"]

    def validate_items(self, items):
        if not items:
            raise serializers.ValidationError("Добавьте хотя бы одно лекарство.")
        pharmacy = self.context["request"].user.pharmacy
        for item in items:
            if item["medicine"].pharmacy_id != pharmacy.id:
                raise serializers.ValidationError(
                    f"Лекарство id={item['medicine'].id} не из вашей аптеки."
                )
            if item.get("unit") == OrderItem.Unit.BLISTER and item["medicine"].blisters < 2:
                raise serializers.ValidationError(f"«{item['medicine'].title}» не продаётся по блистерам.")
        return items

    def create(self, validated_data):
        items = validated_data.pop("items")

        # atomic: если чего-то не хватает, вся продажа отменяется
        with transaction.atomic():
            order = Order.objects.create(**validated_data)
            self._sell(order, items)
        return order

    def update(self, order, validated_data):
        """Изменить продажу: старые позиции возвращаются на склад, новые списываются заново."""
        if order.status == Order.Status.CANCELLED:
            raise serializers.ValidationError({"detail": "Отменённую продажу нельзя изменить."})
        items = validated_data.pop("items", None)

        with transaction.atomic():
            order.discount = validated_data.get("discount", order.discount)
            order.save(update_fields=["discount"])
            if items is None:
                items = [
                    {"medicine": i.medicine, "unit": i.unit, "quantity": i.quantity} for i in order.items.all()
                ]
            for item in order.items.all():
                medicine = Medicine.objects.select_for_update().get(pk=item.medicine_id)
                medicine.give_back(item.unit, item.quantity)
                medicine.save(update_fields=["quantity", "open_blisters"])
            order.items.all().delete()
            self._sell(order, items)
        return order

    def _sell(self, order, items):
        """Списать позиции со склада, создать строки продажи и посчитать итог со скидкой."""
        subtotal = Decimal("0")

        for item in items:
            # select_for_update — блокируем строку, чтобы два кассира не продали один остаток
            medicine = Medicine.objects.select_for_update().get(pk=item["medicine"].pk)
            quantity = item["quantity"]
            unit = item.get("unit", OrderItem.Unit.PACK)

            if not medicine.take(unit, quantity):
                stock = f"{medicine.quantity} уп."
                if medicine.blisters > 1:
                    stock += f" ({medicine.quantity * medicine.blisters + medicine.open_blisters} блист.)"
                raise serializers.ValidationError({"items": f"Недостаточно «{medicine.title}». В наличии: {stock}."})
            medicine.save(update_fields=["quantity", "open_blisters"])

            blister = unit == OrderItem.Unit.BLISTER
            price = medicine.blister_price if blister else medicine.price
            OrderItem.objects.create(
                order=order,
                medicine=medicine,
                unit=unit,
                quantity=quantity,
                price=price,
                packs=(Decimal(quantity) / medicine.blisters if blister else Decimal(quantity)).quantize(Decimal("0.001")),
            )
            subtotal += price * quantity

        total = subtotal * (Decimal("100") - order.discount) / Decimal("100")
        order.total_price = total.quantize(Decimal("0.01"))
        order.save(update_fields=["total_price"])
