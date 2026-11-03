from pathlib import Path

from django.conf import settings
from django.conf.urls.static import static
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import Resolver404, include, path, re_path, resolve
from django.views.generic import TemplateView
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from accounts.views import LoginView, MeView, MyAvatarView, MyReportView, MyWeekView, UserViewSet
from assistant.views import AssistantView
from chat.views import MessageViewSet
from orders.views import OrderViewSet
from pharmacies.views import MedicineViewSet, PharmacyViewSet, SupplierViewSet
from purchases.views import PurchaseViewSet
from reports.views import PurchasesReportView, SalesReportView
from rates.views import RatesView
from writeoffs.views import WriteOffViewSet

STATIC_DIR = Path(__file__).resolve().parent.parent / "frontend" / "static"


class FrontendView(TemplateView):
    """Главная страница. ?v= у css/js — время последней правки, чтобы браузер не брал старые файлы из кэша."""

    template_name = "index.html"

    def get_context_data(self, **kwargs):
        files = (f for f in STATIC_DIR.rglob("*") if f.is_file())
        return {**super().get_context_data(**kwargs), "v": int(max((f.stat().st_mtime for f in files), default=0))}



def not_found(request, path=""):
    """
    Неизвестный адрес: вместо отладочной страницы Django браузер уходит на главную,
    а API-клиент получает обычный JSON 404.
    """
    if not request.path.endswith("/"):  # /api/medicines -> /api/medicines/, как APPEND_SLASH
        try:
            if resolve(request.path + "/").func is not not_found:
                return redirect(request.path + "/")
        except Resolver404:
            pass
    if "text/html" in request.headers.get("Accept", ""):
        return redirect("/")
    return JsonResponse({"detail": "Не найдено."}, status=404)


router = DefaultRouter()
router.register("pharmacies", PharmacyViewSet)
router.register("users", UserViewSet)
router.register("medicines", MedicineViewSet)
router.register("suppliers", SupplierViewSet)
router.register("purchases", PurchaseViewSet)
router.register("orders", OrderViewSet)
router.register("write-offs", WriteOffViewSet)
router.register("chat", MessageViewSet)

RefreshView = extend_schema_view(
    post=extend_schema(tags=["auth"], summary="Новый access токен по refresh")
)(TokenRefreshView)

urlpatterns = [
    path("", FrontendView.as_view(), name="frontend"),
    path("api/auth/login/", LoginView.as_view(), name="login"),
    path("api/auth/refresh/", RefreshView.as_view(), name="refresh"),
    path("api/auth/me/", MeView.as_view(), name="me"),
    path("api/auth/me/avatar/", MyAvatarView.as_view(), name="my-avatar"),
    path("api/auth/me/report/", MyReportView.as_view(), name="my-report"),
    path("api/auth/me/week/", MyWeekView.as_view(), name="my-week"),
    path("api/assistant/", AssistantView.as_view(), name="assistant"),
    path("api/rates/", RatesView.as_view(), name="rates"),
    path("api/reports/sales/", SalesReportView.as_view(), name="report-sales"),
    path("api/reports/purchases/", PurchasesReportView.as_view(), name="report-purchases"),
    path("api/", include(router.urls)),
    *static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT),  # фото и видео чата (в DEBUG; на проде — nginx)
    re_path(r"^(?P<path>.*)$", not_found),  # последним: всё, что не совпало выше
]
