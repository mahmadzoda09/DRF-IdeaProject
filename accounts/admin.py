from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ("username", "full_name", "role", "pharmacy", "is_active")
    list_filter = ("role", "pharmacy", "is_active")
    search_fields = ("username", "full_name", "phone_number")
    fieldsets = UserAdmin.fieldsets + (
        ("Аптека", {"fields": ("full_name", "phone_number", "role", "pharmacy")}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Аптека", {"fields": ("full_name", "phone_number", "role", "pharmacy")}),
    )
