from pathlib import Path

from rest_framework import serializers

from .models import Message, Reaction

MAX_FILE_MB = 25
# тип берём по расширению, а не по content_type от браузера: svg/html под видом картинки — это XSS
EXTENSIONS = {
    Message.Kind.IMAGE: {".jpg", ".jpeg", ".png", ".gif", ".webp"},
    Message.Kind.VIDEO: {".mp4", ".webm", ".mov", ".m4v"},
}
# запись с микрофона: Chrome/Firefox — webm/ogg, Safari — mp4/m4a
VOICE_EXTENSIONS = {".webm", ".ogg", ".mp4", ".m4a", ".mp3", ".wav"}
MAX_VOICE_SECONDS = 600


class MessageSerializer(serializers.ModelSerializer):
    author = serializers.SerializerMethodField()
    author_avatar = serializers.SerializerMethodField()
    role = serializers.CharField(source="user.role", read_only=True, default=None)
    file = serializers.FileField(write_only=True, required=False)
    file_url = serializers.SerializerMethodField()
    voice = serializers.BooleanField(write_only=True, required=False, default=False, help_text="файл — голосовое с микрофона")
    reply = serializers.SerializerMethodField()
    reactions = serializers.SerializerMethodField()
    edited = serializers.SerializerMethodField()
    pinned = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            "id", "pharmacy", "user", "author", "author_avatar", "role", "text", "kind", "file", "file_url", "voice", "duration",
            "reply_to", "reply", "reactions", "edited", "deleted", "pinned", "created_at",
        ]
        read_only_fields = ["pharmacy", "user", "kind", "deleted", "created_at"]
        extra_kwargs = {
            "text": {"required": False},
            "duration": {"max_value": MAX_VOICE_SECONDS},
            "reply_to": {"write_only": True, "required": False},
        }

    @staticmethod
    def author_of(message):
        return (message.user.full_name or message.user.username) if message.user else None

    def get_author(self, message) -> str | None:
        return self.author_of(message)

    def get_author_avatar(self, message) -> str | None:
        return message.user.avatar.url if message.user and message.user.avatar else None

    def get_reply(self, message) -> dict | None:
        """Цитата сообщения, на которое ответили: кто и начало текста (или тип вложения)."""
        r = message.reply_to
        if r is None:
            return None
        return {"id": r.id, "user": r.user_id, "author": self.author_of(r), "text": r.text[:120], "kind": r.kind, "deleted": r.deleted}

    def get_reactions(self, message) -> list:
        # [{"emoji": "👍", "users": [3, 7]}] — «моя» ли реакция, решает клиент (рассылка общая для всех)
        grouped = {}
        for reaction in message.reactions.all():
            grouped.setdefault(reaction.emoji, []).append(reaction.user_id)
        return [{"emoji": emoji, "users": users} for emoji, users in grouped.items()]

    def get_edited(self, message) -> bool:
        return message.edited_at is not None

    def get_pinned(self, message) -> bool:
        return message.pinned_at is not None

    def get_file_url(self, message) -> str | None:
        # относительная ссылка: одинаково работает за прокси и в WebSocket-рассылке
        return message.file.url if message.file else None

    def validate_file(self, file):
        if file.size > MAX_FILE_MB * 1024 * 1024:
            raise serializers.ValidationError(f"Файл больше {MAX_FILE_MB} МБ.")
        return file

    def validate(self, data):
        data["text"] = data.get("text", "").strip()
        if not data["text"] and not data.get("file"):
            raise serializers.ValidationError({"text": "Пустое сообщение."})
        voice = data.pop("voice", False)
        file = data.get("file")
        if not file:
            data.pop("duration", None)
            return data
        ext = Path(file.name).suffix.lower()
        if voice:
            if ext not in VOICE_EXTENSIONS:
                raise serializers.ValidationError({"file": "Голосовое — только запись с микрофона (webm, ogg, mp4, m4a, mp3, wav)."})
            data["kind"] = Message.Kind.VOICE
            return data
        kind = next((kind for kind, allowed in EXTENSIONS.items() if ext in allowed), None)
        if kind is None:
            raise serializers.ValidationError({"file": "Можно отправить только фото (jpg, png, gif, webp) или видео (mp4, webm, mov)."})
        data["kind"] = kind
        data.pop("duration", None)
        return data
