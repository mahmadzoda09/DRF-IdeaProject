from datetime import date

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from accounts.mixins import MonthFilterMixin, PharmacyScopedMixin
from accounts.permissions import HasRolePermission
from accounts.rbac import Perm, has_permission
from pharmacies.models import IN_STOCK, Medicine

from .models import WriteOff
from .serializers import PendingSerializer, WriteOffCreateSerializer, WriteOffSerializer
from .services import pending_by_month, write_off_expired


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter("month", OpenApiTypes.STR, description="месяц списания ГГГГ-ММ"),
            OpenApiParameter("month_from", OpenApiTypes.STR, description="с месяца ГГГГ-ММ (включительно)"),
            OpenApiParameter("month_to", OpenApiTypes.STR, description="по месяц ГГГГ-ММ (включительно)"),
        ]
    )
)
@extend_schema(tags=["writeoffs"])
class WriteOffViewSet(
    MonthFilterMixin,
    PharmacyScopedMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    Списание просроченных лекарств. Смотреть: админ и директор. Списывать: только директор.
    Остаток уменьшается, в выручку и продажи не попадает.
    """

    queryset = WriteOff.objects.select_related("user").prefetch_related("items__medicine")
    serializer_class = WriteOffSerializer
    permission_classes = [HasRolePermission]
    required_permissions = {
        "create": Perm.WRITEOFF_CREATE,
        "*": Perm.WRITEOFF_VIEW,
    }
    search_fields = ["write_off_number", "items__medicine__title"]
    ordering_fields = ["created_at", "total_cost"]

    @extend_schema(summary="Просроченное на складе по месяцам срока — что можно списать", responses=PendingSerializer)
    @action(detail=False)
    def pending(self, request):
        medicines = Medicine.objects.filter(IN_STOCK)
        if not has_permission(request.user, Perm.ALL_PHARMACIES):
            medicines = medicines.filter(pharmacy=request.user.pharmacy)
        return Response(PendingSerializer(pending_by_month(list(medicines))).data)

    @extend_schema(
        summary="Списать просроченное: за месяц срока или всё",
        request=WriteOffCreateSerializer,
        responses={201: WriteOffSerializer},
    )
    def create(self, request):
        data = WriteOffCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        month = data.validated_data.get("month")
        month = date(int(month[:4]), int(month[5:]), 1) if month else None
        write_off = write_off_expired(request.user.pharmacy, request.user, month)
        if write_off is None:
            raise ValidationError({"detail": "Просроченных лекарств для списания нет."})
        write_off = self.get_queryset().get(pk=write_off.pk)
        return Response(self.get_serializer(write_off).data, status=status.HTTP_201_CREATED)
