import re

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import Medicine, Pharmacy, Supplier


class PharmacySerializer(serializers.ModelSerializer):
    staff_count = serializers.SerializerMethodField()

    class Meta:
        model = Pharmacy
        fields = ["id", "name", "address", "phone_number", "staff_count", "created_at"]
        read_only_fields = ["created_at"]

    def get_staff_count(self, pharmacy) -> int:
        # в списке — из аннотации одним запросом; после создания/изменения — отдельным
        count = getattr(pharmacy, "staff_count", None)
        return pharmacy.users.count() if count is None else count


class LowStockSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    quantity = serializers.IntegerField()


class ExpiringSoonSerializer(serializers.Serializer):
    medicine_id = serializers.IntegerField()
    medicine__title = serializers.CharField()
    quantity = serializers.IntegerField()
    expire_date = serializers.DateField()


class DailySalesSerializer(serializers.Serializer):
    date = serializers.DateField()
    total = serializers.DecimalField(max_digits=12, decimal_places=2)
    count = serializers.IntegerField()


class WeekDaySerializer(DailySalesSerializer):
    future = serializers.BooleanField(help_text="день ещё не наступил")


class WeekSerializer(serializers.Serializer):
    start = serializers.DateField(help_text="понедельник")
    end = serializers.DateField(help_text="воскресенье")
    days = WeekDaySerializer(many=True)
    best = WeekDaySerializer(allow_null=True, help_text="день с наибольшей выручкой; null — продаж не было")
    worst = WeekDaySerializer(allow_null=True, help_text="день с наименьшей выручкой среди прошедших, кроме best")
    total = serializers.DecimalField(max_digits=12, decimal_places=2)
    count = serializers.IntegerField()
    prev_total = serializers.DecimalField(max_digits=12, decimal_places=2, help_text="выручка предыдущей недели")
    has_next = serializers.BooleanField(help_text="есть ли следующая неделя (не в будущем)")


class FinanceSerializer(serializers.Serializer):
    sales = serializers.DecimalField(max_digits=12, decimal_places=2)
    expenses = serializers.DecimalField(max_digits=12, decimal_places=2, help_text="себестоимость проданного")
    profit = serializers.DecimalField(max_digits=12, decimal_places=2)


class MonthlySerializer(serializers.Serializer):
    month = serializers.DateField(help_text="первое число месяца")
    sales = serializers.DecimalField(max_digits=12, decimal_places=2)
    expenses = serializers.DecimalField(
        max_digits=12, decimal_places=2, help_text="себестоимость проданного (средняя закупочная цена × кол-во)"
    )
    profit = serializers.DecimalField(max_digits=12, decimal_places=2, help_text="продажи − расходы")
    purchases = serializers.DecimalField(max_digits=12, decimal_places=2, help_text="потрачено на закупки")


class RankedMedicineSerializer(serializers.Serializer):
    medicine_id = serializers.IntegerField()
    medicine__title = serializers.CharField()
    quantity = serializers.IntegerField(help_text="остаток сейчас")
    sold = serializers.FloatField(help_text="продано упаковок (блистеры — долями упаковки)")
    revenue = serializers.DecimalField(max_digits=12, decimal_places=2, help_text="сумма по ценам продажи, без скидки чека")


class MedicineRankingSerializer(serializers.Serializer):
    best = RankedMedicineSerializer(many=True, help_text="самые продаваемые")
    worst = RankedMedicineSerializer(many=True, help_text="наименее продаваемые, включая непроданные")


class PharmacyStatsSerializer(serializers.Serializer):
    """Только для Swagger: формат ответа /pharmacies/{id}/stats/."""

    pharmacy = serializers.CharField()
    orders_count = serializers.IntegerField()
    total_sales = serializers.DecimalField(max_digits=12, decimal_places=2)
    today_sales = serializers.DecimalField(max_digits=12, decimal_places=2)
    low_stock = LowStockSerializer(many=True)
    expiring_soon = ExpiringSoonSerializer(many=True)
    sales_by_day = DailySalesSerializer(many=True)
    monthly = MonthlySerializer(many=True)
    medicine_ranking = MedicineRankingSerializer()
    week = WeekSerializer(help_text="текущая неделя")


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = ["id", "pharmacy", "name", "phone_number"]
        read_only_fields = ["pharmacy"]


class BatchSerializer(serializers.Serializer):
    expire_date = serializers.DateField()
    quantity = serializers.IntegerField()


class MedicineSerializer(serializers.ModelSerializer):
    # срок годности остатка (см. pharmacies/expiry.py); считается во вьюхе и передаётся через context["expiry"]
    expire_date = serializers.SerializerMethodField(help_text="ближайший срок годности среди партий на складе")
    expire_status = serializers.SerializerMethodField(help_text="expired / soon (≤30 дней) / ok / null — неизвестно")
    batches = serializers.SerializerMethodField(help_text="партии на складе по FIFO: срок и количество")
    blister_price = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True, help_text="цена одного блистера")

    class Meta:
        model = Medicine
        fields = [
            "id", "pharmacy", "title", "barcode", "price", "quantity", "blisters", "open_blisters", "blister_price",
            "expire_date", "expire_status", "batches", "created_at",
        ]
        # остаток нельзя менять вручную — только через закупки и продажи
        read_only_fields = ["pharmacy", "quantity", "open_blisters", "created_at"]

    def _expiry(self, obj):
        info = self.context.get("expiry")
        if info is None or obj.id not in info:
            from .expiry import expiry_info

            info = expiry_info([obj])
        return info[obj.id]

    def get_expire_date(self, obj) -> str | None:
        date = self._expiry(obj)["expire_date"]
        return date.isoformat() if date else None

    def get_expire_status(self, obj) -> str | None:
        return self._expiry(obj)["expire_status"]

    @extend_schema_field(BatchSerializer(many=True))
    def get_batches(self, obj):
        return BatchSerializer(self._expiry(obj)["batches"], many=True).data

    def validate_barcode(self, value):
        value = value.strip()
        if not value:
            return value
        if not re.fullmatch(r"[0-9A-Za-z\-]+", value):
            raise serializers.ValidationError("Штрих-код: только цифры, латинские буквы и дефис.")
        pharmacy = self.context["request"].user.pharmacy
        queryset = Medicine.objects.filter(pharmacy=pharmacy, barcode=value)
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError("Этот штрих-код уже назначен другому лекарству.")
        return value

    def validate_blisters(self, value):
        # блистеры вскрытой упаковки посчитаны по старому делению — сначала их нужно продать
        if self.instance and self.instance.open_blisters and value != self.instance.blisters:
            raise serializers.ValidationError(
                f"Есть вскрытая упаковка ({self.instance.open_blisters} блист.) — продайте её, потом меняйте число блистеров."
            )
        return value

    def validate_title(self, value):
        pharmacy = self.context["request"].user.pharmacy
        queryset = Medicine.objects.filter(pharmacy=pharmacy, title__iexact=value)
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError("Такое лекарство уже есть в вашей аптеке.")
        return value


class ExpiryBatchSerializer(serializers.Serializer):
    medicine_id = serializers.IntegerField()
    title = serializers.CharField()
    barcode = serializers.CharField()
    pharmacy = serializers.IntegerField()
    price = serializers.DecimalField(max_digits=10, decimal_places=2)
    quantity = serializers.IntegerField(help_text="упаковок этой партии на складе (FIFO)")
    expire_date = serializers.DateField()
    days_left = serializers.IntegerField(help_text="дней до конца срока; меньше 0 — срок истёк")
    status = serializers.CharField(help_text="expired / soon (≤30 дней) / ok")


class ExpiryCountSerializer(serializers.Serializer):
    batches = serializers.IntegerField()
    quantity = serializers.IntegerField()


class ExpirySummarySerializer(serializers.Serializer):
    expired = ExpiryCountSerializer()
    soon = ExpiryCountSerializer(help_text="0–30 дней")
    quarter = ExpiryCountSerializer(help_text="31–90 дней")
    total = ExpiryCountSerializer()


class ExpiryReportSerializer(serializers.Serializer):
    today = serializers.DateField()
    summary = ExpirySummarySerializer()
    batches = ExpiryBatchSerializer(many=True)
