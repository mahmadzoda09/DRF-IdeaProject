from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets

from accounts.mixins import MonthFilterMixin, PharmacyScopedMixin
from accounts.permissions import HasRolePermission
from accounts.rbac import Perm

from .models import Purchase
from .serializers import PurchaseSerializer


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
    viewsets.GenericViewSet,
):
    """
    Закупки (приход товара).
    Создать: только директор. Остаток лекарств увеличивается автоматически.
    Смотреть: админ проекта и директор.
    """

    queryset = Purchase.objects.select_related("supplier", "user").prefetch_related("items__medicine")
    serializer_class = PurchaseSerializer
    permission_classes = [HasRolePermission]
    required_permissions = {
        "create": Perm.PURCHASE_CREATE,
        "*": Perm.PURCHASE_VIEW,
    }
    search_fields = ["purchase_number", "supplier__name"]
    ordering_fields = ["created_at", "total_price"]

    def perform_create(self, serializer):
        serializer.save(pharmacy=self.request.user.pharmacy, user=self.request.user)
