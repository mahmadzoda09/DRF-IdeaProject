from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Админ проекта"
        DIRECTOR = "director", "Директор аптеки"
        PHARMACIST = "pharmacist", "Фармацевт"

    pharmacy = models.ForeignKey(
        "pharmacies.Pharmacy",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="users",
    )
    full_name = models.CharField(max_length=255, blank=True)
    phone_number = models.CharField(max_length=20, unique=True, null=True, blank=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.PHARMACIST)
    # личные отличия от прав роли (см. accounts/rbac.py: GRANTABLE): что фармацевту дали сверх роли и что отняли
    extra_permissions = models.JSONField(default=list, blank=True)
    revoked_permissions = models.JSONField(default=list, blank=True)
    avatar = models.FileField(upload_to="avatars/", blank=True)  # всегда jpg 640×640 (accounts/avatars.py)
    # админ или директор запретил писать в чат аптеки (читать можно)
    chat_muted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def save(self, *args, **kwargs):
        if self.is_superuser:
            self.role = self.Role.ADMIN
            self.pharmacy = None
        super().save(*args, **kwargs)

    @property
    def is_project_admin(self):
        return self.role == self.Role.ADMIN

    @property
    def is_director(self):
        return self.role == self.Role.DIRECTOR

    @property
    def is_pharmacist(self):
        return self.role == self.Role.PHARMACIST

    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"
