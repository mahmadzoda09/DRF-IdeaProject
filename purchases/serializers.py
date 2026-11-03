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
            total = Decimal("0")

            for item in items:
                sale_price = item.pop("sale_price", None)
                medicine = Medicine.objects.select_for_update().get(pk=item["medicine"].pk)
                medicine.quantity += item["quantity"]  # приход на склад
                update_fields = ["quantity"]
                if sale_price is not None and sale_price != medicine.price:
                    medicine.price = sale_price
                    update_fields.append("price")
                medicine.save(update_fields=update_fields)

                PurchaseItem.objects.create(purchase=purchase, **item)
                total += item["price"] * item["quantity"]

            purchase.total_price = total
            purchase.save(update_fields=["total_price"])

        return purchase
