import uuid
from pathlib import Path

from django.conf import settings
from django.db import models


def attachment_path(message, filename):
    # случайное имя: ссылку на файл нельзя угадать перебором
    return f"chat/{message.pharmacy_id}/{uuid.uuid4().hex}{Path(filename).suffix.lower()[:10]}"


class Message(models.Model):
    """Сообщение в чате аптеки: пишут директор и фармацевты, админ проекта — в любую аптеку."""

    class Kind(models.TextChoices):
        TEXT = "text", "Текст"
        IMAGE = "image", "Фото"
        VIDEO = "video", "Видео"
        VOICE = "voice", "Голосовое"

    pharmacy = models.ForeignKey("pharmacies.Pharmacy", on_delete=models.CASCADE, related_name="messages")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="messages")
    text = models.TextField(max_length=2000, blank=True)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.TEXT)
    file = models.FileField(upload_to=attachment_path, blank=True)
    # длительность голосового в секундах: у записи из браузера (webm) её часто нет в самом файле
    duration = models.PositiveIntegerField(null=True, blank=True)
    reply_to = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="replies")
    edited_at = models.DateTimeField(null=True, blank=True)
    # удалённое остаётся строкой «Сообщение удалено» (на него могут быть ответы); текст и файл стираются
    deleted = models.BooleanField(default=False)
    # закреплённое сообщение аптеки — не больше одного
    pinned_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.user}: {self.text[:30] or self.kind}"


class Reaction(models.Model):
    """Реакция на сообщение: у сотрудника одна реакция на сообщение (повторный клик — снять, другой эмодзи — заменить)."""

    EMOJI = ["👍", "❤️", "😂", "😮", "😢", "🙏", "🔥", "✅"]

    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="reactions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chat_reactions")
    emoji = models.CharField(max_length=8)

    class Meta:
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["message", "user"], name="chat_reaction_once")]


class ChatRead(models.Model):
    """До какого сообщения сотрудник прочитал чат своей аптеки — для синих галочек «прочитали все»."""

    pharmacy = models.ForeignKey("pharmacies.Pharmacy", on_delete=models.CASCADE, related_name="chat_reads")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chat_reads")
    last_read_id = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["pharmacy", "user"], name="chat_read_once")]
