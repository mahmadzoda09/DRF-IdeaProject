from django.contrib import admin

from .models import Medicine, Pharmacy, Supplier


@admin.register(Pharmacy)
class PharmacyAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "address", "phone_number", "created_at")
    search_fields = ("name",)


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "phone_number", "pharmacy")
    list_filter = ("pharmacy",)


@admin.register(Medicine)
class MedicineAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "price", "quantity", "pharmacy")
    list_filter = ("pharmacy",)
    search_fields = ("title",)
