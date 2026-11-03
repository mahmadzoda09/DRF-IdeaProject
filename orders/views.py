from django.db import transaction
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from accounts.mixins import MonthFilterMixin, PharmacyScopedMixin
from accounts.permissions import HasRolePermission
from accounts.rbac import Perm, has_permission
from pharmacies.models import Medicine

from .models import Order
from .serializers import OrderSerializer


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter("status", OpenApiTypes.STR, enum=Order.Status.values),
            OpenApiParameter("month", OpenApiTypes.STR, description="один месяц ГГГГ-ММ, например 2026-10"),
            OpenApiParameter("month_from", OpenApiTypes.STR, description="с месяца ГГГГ-ММ (включительно)"),
            OpenApiParameter("month_to", OpenApiTypes.STR, description="по месяц ГГГГ-ММ (включительно)"),
        ]
    )
)
@extend_schema(tags=["orders"])
class OrderViewSet(
    MonthFilterMixin,
    PharmacyScopedMixin,
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """
    Продажи.
    Создать: директор и фармацевт.
    Смотреть: админ (все), директор (вся аптека), фармацевт (только свои продажи).
    Изменить и отменить: только директор (товар возвращается на склад).
    """

    queryset = Order.objects.select_related("user").prefetch_related("items__medicine")
    serializer_class = OrderSerializer
    permission_classes = [HasRolePermission]
    required_permissions = {
        "create": Perm.ORDER_CREATE,
        "cancel": Perm.ORDER_CANCEL,
        "update": Perm.ORDER_CANCEL,
        "partial_update": Perm.ORDER_CANCEL,
        "*": Perm.ORDER_VIEW,
    }
    search_fields = ["order_number"]
    ordering_fields = ["created_at", "total_price"]

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if not getattr(self, "swagger_fake_view", False) and not has_permission(
            user, Perm.ORDER_VIEW_ALL
        ):
            queryset = queryset.filter(user=user)
        status = self.request.query_params.get("status")
        if status:
            queryset = queryset.filter(status=status)
        return queryset

    def perform_create(self, serializer):
        serializer.save(pharmacy=self.request.user.pharmacy, user=self.request.user)

    @extend_schema(summary="Отменить продажу (товар вернётся на склад)", request=None)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        order = self.get_object()
        if order.status == Order.Status.CANCELLED:
            raise ValidationError({"detail": "Заказ уже отменён."})

        with transaction.atomic():
            for item in order.items.all():
                medicine = Medicine.objects.select_for_update().get(pk=item.medicine_id)
                medicine.give_back(item.unit, item.quantity)
                medicine.save(update_fields=["quantity", "open_blisters"])
            order.status = Order.Status.CANCELLED
            order.save(update_fields=["status"])

        return Response(self.get_serializer(order).data)
