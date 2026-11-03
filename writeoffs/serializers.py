import re

from rest_framework import serializers

from .models import WriteOff, WriteOffItem


class WriteOffItemSerializer(serializers.ModelSerializer):
    medicine_title = serializers.CharField(source="medicine.title", read_only=True)

    class Meta:
        model = WriteOffItem
        fields = ["id", "medicine", "medicine_title", "quantity", "cost", "price", "expire_date"]


class WriteOffSerializer(serializers.ModelSerializer):
    items = WriteOffItemSerializer(many=True, read_only=True)
    user_name = serializers.SerializerMethodField()

    class Meta:
        model = WriteOff
        fields = [
            "id",
            "write_off_number",
            "pharmacy",
            "user",
            "user_name",
            "expire_month",
            "total_cost",
            "total_price",
            "created_at",
            "items",
        ]

    def get_user_name(self, obj) -> str | None:
        return (obj.user.full_name or obj.user.username) if obj.user else None


class WriteOffCreateSerializer(serializers.Serializer):
    month = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, help_text="месяц срока ГГГГ-ММ; пусто — всё просроченное"
    )

    def validate_month(self, value):
        if not value:
            return None
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
            raise serializers.ValidationError("Укажите месяц в формате ГГГГ-ММ, например 2026-09.")
        return value


class PendingItemSerializer(serializers.Serializer):
    medicine_id = serializers.IntegerField()
    title = serializers.CharField()
    barcode = serializers.CharField()
    pharmacy = serializers.IntegerField()
    quantity = serializers.IntegerField()
    cost = serializers.DecimalField(max_digits=10, decimal_places=2)
    price = serializers.DecimalField(max_digits=10, decimal_places=2)
    expire_date = serializers.DateField()
    days_overdue = serializers.IntegerField()


class PendingTotalSerializer(serializers.Serializer):
    batches = serializers.IntegerField()
    quantity = serializers.IntegerField()
    cost = serializers.DecimalField(max_digits=12, decimal_places=2)
    price = serializers.DecimalField(max_digits=12, decimal_places=2)


class PendingMonthSerializer(PendingTotalSerializer):
    month = serializers.CharField()
    items = PendingItemSerializer(many=True)


class PendingSerializer(serializers.Serializer):
    today = serializers.DateField()
    total = PendingTotalSerializer()
    months = PendingMonthSerializer(many=True)
