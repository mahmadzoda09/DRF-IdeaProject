from django.apps import AppConfig
from django.db.backends.signals import connection_created


class PharmaciesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "pharmacies"

    def ready(self):
        from .sqlite_unicode import register

        # поиск по кириллице без учёта регистра на SQLite (см. sqlite_unicode.py)
        connection_created.connect(register, dispatch_uid="pharmacies.sqlite_unicode_like")
