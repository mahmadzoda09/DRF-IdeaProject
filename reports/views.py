from urllib.parse import quote

from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import HasRolePermission
from accounts.rbac import Perm

from .builder import file_name, purchases_report, sales_report
from .export import CONTENT_TYPES, EXPORTERS, FontNotFound

PARAMETERS = [
    OpenApiParameter("month_from", OpenApiTypes.STR, description="с месяца ГГГГ-ММ (включительно)"),
    OpenApiParameter("month_to", OpenApiTypes.STR, description="по месяц ГГГГ-ММ (включительно)"),
    OpenApiParameter("pharmacy", OpenApiTypes.INT, description="только для админа: аптека; без него — все аптеки"),
    OpenApiParameter("export", OpenApiTypes.STR, enum=list(EXPORTERS), description="скачать файлом; без него — JSON"),
    OpenApiParameter(
        "section",
        OpenApiTypes.STR,
        description="только одна часть отчёта: months, medicines, sellers, suppliers, list (summary — итоги); без него — весь отчёт",
    ),
]


def only_section(report, section):
    """Оставляет в отчёте одну секцию (кнопка «По месяцам» и т.п.); её название идёт в заголовок."""
    found = [s for s in report["sections"] if s["id"] == section]
    if not found:
        raise ValidationError({"section": f"Допустимо: {', '.join(s['id'] for s in report['sections'])}."})
    report["title"] = f"{report['title']}. {found[0]['title']}"
    report["section"] = section
    report["sections"] = found
    return report


class ReportView(APIView):
    """Отчёт за период: JSON для страницы или файл (?export=xlsx|docx|pdf)."""

    permission_classes = [HasRolePermission]
    build = None

    def get(self, request):
        report = type(self).build(request.user, request.query_params, request.LANGUAGE_CODE)
        section = request.query_params.get("section")
        if section:
            report = only_section(report, section)
        export = request.query_params.get("export")
        if not export:
            return Response(report)
        if export not in EXPORTERS:
            raise ValidationError({"export": f"Допустимо: {', '.join(EXPORTERS)}."})
        try:
            content = EXPORTERS[export](report, request.LANGUAGE_CODE)
        except FontNotFound as e:
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        name = f"{file_name(report, request.query_params)}.{export}"
        response = HttpResponse(content, content_type=CONTENT_TYPES[export])
        response["Content-Disposition"] = f"attachment; filename=\"{name}\"; filename*=UTF-8''{quote(name)}"
        return response


@extend_schema(tags=["reports"], summary="Отчёт о продажах", parameters=PARAMETERS, responses=OpenApiTypes.OBJECT)
class SalesReportView(ReportView):
    """Фармацевт — только свои продажи; себестоимость и прибыль — директору и админу."""

    required_permissions = {"*": Perm.ORDER_VIEW}
    build = staticmethod(sales_report)


@extend_schema(tags=["reports"], summary="Отчёт о закупках", parameters=PARAMETERS, responses=OpenApiTypes.OBJECT)
class PurchasesReportView(ReportView):
    required_permissions = {"*": Perm.PURCHASE_VIEW}
    build = staticmethod(purchases_report)
