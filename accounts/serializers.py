from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from pharmacies.serializers import DailySalesSerializer, FinanceSerializer, MedicineRankingSerializer, WeekSerializer

from .models import User
from .rbac import Perm, get_permissions, has_permission


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, min_length=6)
    pharmacy_name = serializers.CharField(source="pharmacy.name", read_only=True, default=None)
    permissions = serializers.SerializerMethodField()
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "password",
            "full_name",
            "phone_number",
            "role",
            "pharmacy",
            "pharmacy_name",
            "permissions",
            "avatar_url",
            "chat_muted",
            "is_active",
            "created_at",
        ]
        read_only_fields = ["created_at", "chat_muted"]

    def get_permissions(self, obj) -> list[str]:
        return sorted(get_permissions(obj))

    def get_avatar_url(self, obj) -> str | None:
        return obj.avatar.url if obj.avatar else None

    def validate(self, attrs):
        me = self.context["request"].user

        if self.instance is None and not attrs.get("password"):
            raise serializers.ValidationError({"password": "Пароль обязателен."})

        # Полное управление пользователями (админ проекта): любая роль, любая аптека
        if has_permission(me, Perm.USER_MANAGE):
            role = attrs.get("role", self.instance.role if self.instance else None)
            pharmacy = attrs.get("pharmacy", self.instance.pharmacy if self.instance else None)

            if self.instance is None and "role" not in attrs:
                raise serializers.ValidationError({"role": "Укажите роль."})
            if role == User.Role.ADMIN and pharmacy:
                raise serializers.ValidationError({"pharmacy": "У админа проекта не может быть аптеки."})
            if role != User.Role.ADMIN and not pharmacy:
                raise serializers.ValidationError({"pharmacy": "Для сотрудника аптеки укажите аптеку."})
            return attrs

        # Управление фармацевтами (директор): только фармацевты и только в своей аптеке
        if has_permission(me, Perm.PHARMACIST_MANAGE):
            attrs["role"] = User.Role.PHARMACIST
            attrs["pharmacy"] = me.pharmacy
            return attrs

        raise serializers.ValidationError({"detail": "Недостаточно прав."})

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        instance = super().update(instance, validated_data)
        if password:
            instance.set_password(password)
            instance.save()
        return instance


class LoginResponseSerializer(serializers.Serializer):
    """Только для Swagger: формат ответа логина."""

    access = serializers.CharField()
    refresh = serializers.CharField()
    user = UserSerializer()


class LoginSerializer(TokenObtainPairSerializer):
    """Логин возвращает токены + данные пользователя (роль, аптека, права)."""

    def validate(self, attrs):
        data = super().validate(attrs)
        data["user"] = UserSerializer(self.user).data
        return data


# ---------------------------------------------------------------- Только для Swagger: формат /auth/me/report/


class _SalesSummarySerializer(serializers.Serializer):
    count = serializers.IntegerField(help_text="завершённых продаж за период")
    total = serializers.DecimalField(max_digits=12, decimal_places=2)
    avg_check = serializers.DecimalField(max_digits=12, decimal_places=2)
    items_sold = serializers.FloatField(help_text="продано упаковок (блистеры — долями упаковки)")
    cancelled = serializers.IntegerField(help_text="отменённых продаж за период")


class _CountTotalSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    total = serializers.DecimalField(max_digits=12, decimal_places=2)


class _RecentOrderSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    order_number = serializers.CharField()
    created_at = serializers.DateTimeField()
    total_price = serializers.DecimalField(max_digits=12, decimal_places=2)
    status = serializers.CharField()
    items_count = serializers.IntegerField()


class MyReportSerializer(serializers.Serializer):
    sales = _SalesSummarySerializer()
    today = _CountTotalSerializer()
    sales_by_day = DailySalesSerializer(many=True, help_text="последние 14 дней, не зависит от периода")
    medicine_ranking = MedicineRankingSerializer(help_text="лучшие и худшие по моим продажам за период")
    recent_orders = _RecentOrderSerializer(many=True)
    purchases = _CountTotalSerializer(allow_null=True, help_text="null, если пользователь не оформляет закупки")
    week = WeekSerializer(help_text="текущая неделя, мои продажи")
    finance = FinanceSerializer(help_text="мои продажи за период = расходы (себестоимость) + прибыль")


class AccessItemSerializer(serializers.Serializer):
    perm = serializers.CharField()
    granted = serializers.BooleanField(help_text="может сейчас")
    default = serializers.BooleanField(help_text="есть у роли «фармацевт» по умолчанию")


class AccessGroupSerializer(serializers.Serializer):
    key = serializers.CharField(help_text="sales / medicines / purchases / other")
    items = AccessItemSerializer(many=True)


class AccessSerializer(serializers.Serializer):
    user = serializers.DictField()
    groups = AccessGroupSerializer(many=True)
    requires = serializers.DictField(child=serializers.ListField(child=serializers.CharField()), help_text="право → без чего не работает")


class AccessUpdateSerializer(serializers.Serializer):
    permissions = serializers.ListField(child=serializers.CharField(), help_text="все права, которые должны быть у фармацевта")

    def validate_permissions(self, value):
        from .rbac import GRANTABLE_ALL

        unknown = sorted(set(value) - set(GRANTABLE_ALL))
        if unknown:
            raise serializers.ValidationError(f"Эти права фармацевту дать нельзя: {', '.join(unknown)}.")
        return value


class AvatarUploadSerializer(serializers.Serializer):
    """Только для Swagger: загрузка аватарки (multipart)."""

    file = serializers.FileField()
