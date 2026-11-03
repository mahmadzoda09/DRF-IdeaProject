from django.contrib import admin

from .models import Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("order_number", "pharmacy", "user", "total_price", "status", "created_at")
    list_filter = ("pharmacy", "status")
    search_fields = ("order_number",)
    inlines = [OrderItemInline]
