from django.db import transaction
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets

from accounts.mixins import MonthFilterMixin, PharmacyScopedMixin
from accounts.permissions import HasRolePermission
from accounts.rbac import Perm

from .models import Purchase
from .serializers import PurchaseSerializer, check_editable, move_stock, stock_deltas


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter("month", OpenApiTypes.STR, description="один месяц ГГГГ-ММ, например 2026-10"),
            OpenApiParameter("month_from", OpenApiTypes.STR, description="с месяца ГГГГ-ММ (включительно)"),
            OpenApiParameter("month_to", OpenApiTypes.STR, description="по месяц ГГГГ-ММ (включительно)"),
        ]
    )
)
@extend_schema(tags=["purchases"])
class PurchaseViewSet(
    MonthFilterMixin,
    PharmacyScopedMixin,
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    Закупки (приход товара).
    Создать: только директор. Остаток лекарств увеличивается автоматически.
    Смотреть: админ проекта и директор.
    Изменить и удалить: только директор (остатки пересчитываются; уже проданное убрать нельзя).
    """

    queryset = Purchase.objects.select_related("supplier", "user").prefetch_related("items__medicine")
    serializer_class = PurchaseSerializer
    permission_classes = [HasRolePermission]
    required_permissions = {
        "create": Perm.PURCHASE_CREATE,
        "update": Perm.PURCHASE_CREATE,
        "partial_update": Perm.PURCHASE_CREATE,
        "destroy": Perm.PURCHASE_CREATE,
        "*": Perm.PURCHASE_VIEW,
    }
    search_fields = ["purchase_number", "supplier__name"]
    ordering_fields = ["created_at", "total_price"]

    def perform_create(self, serializer):
        serializer.save(pharmacy=self.request.user.pharmacy, user=self.request.user)

    def perform_destroy(self, purchase):
        """Удалить закупку: её товар уходит со склада."""
        with transaction.atomic():
            check_editable(purchase)
            move_stock(stock_deltas(purchase.items.all(), sign=-1))
            purchase.delete()
