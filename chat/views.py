from django.db import transaction
from django.db.models import Count, Max, Q
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from accounts.avatars import delete_avatar, save_avatar

from accounts.models import User
from accounts.rbac import Perm, has_permission
from pharmacies.models import Pharmacy

from .models import ChatRead, Message, Reaction
from .serializers import MessageSerializer
from .services import (
    broadcast,
    broadcast_cleared,
    broadcast_info,
    broadcast_mute,
    broadcast_pin,
    broadcast_update,
    chat_pharmacy,
    mark_read,
)


class ReadSerializer(serializers.Serializer):
    last_id = serializers.IntegerField(min_value=0)


class ReadStateSerializer(serializers.Serializer):
    read_up_to = serializers.IntegerField(help_text="все сотрудники прочитали сообщения с id до этого включительно")


class EditSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=2000, allow_blank=True, trim_whitespace=True)


class ReactSerializer(serializers.Serializer):
    emoji = serializers.ChoiceField(choices=Reaction.EMOJI)


class MuteSerializer(serializers.Serializer):
    muted = serializers.BooleanField()


class MemberSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    avatar_url = serializers.SerializerMethodField()
    last_read_id = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "name", "role", "avatar_url", "chat_muted", "last_read_id"]

    def get_name(self, user) -> str:
        return user.full_name or user.username

    def get_avatar_url(self, user) -> str | None:
        return user.avatar.url if user.avatar else None

    def get_last_read_id(self, user) -> int:
        return self.context["reads"].get(user.id, 0)


class ChatInfoSerializer(serializers.Serializer):
    pharmacy = serializers.IntegerField()
    name = serializers.CharField()
    avatar_url = serializers.CharField(allow_null=True)
    members = serializers.IntegerField()
    counts = serializers.DictField(help_text="image, video, voice, link — сколько в чате")


class MediaItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kind = serializers.CharField()
    file_url = serializers.CharField(allow_null=True)
    duration = serializers.IntegerField(allow_null=True)
    text = serializers.CharField()
    author = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()


class AvatarUploadSerializer(serializers.Serializer):
    file = serializers.FileField()


class OverviewSerializer(serializers.Serializer):
    pharmacy = serializers.IntegerField()
    name = serializers.CharField()
    avatar_url = serializers.CharField(allow_null=True)
    members = serializers.IntegerField()
    muted = serializers.IntegerField()
    messages = serializers.IntegerField()
    last = serializers.DictField(allow_null=True, help_text="последнее сообщение: author, text, kind, created_at")


PHARMACY_PARAM = OpenApiParameter("pharmacy", OpenApiTypes.INT, description="ID аптеки. Только для админа (обязательно)")

# сколько последних сообщений отдаём при открытии чата
HISTORY = 100


def is_admin(user):
    return has_permission(user, Perm.ALL_PHARMACIES)


def can_moderate(user, pharmacy):
    """Удалять чужие сообщения, закреплять, запрещать писать: админ проекта и директор этой аптеки."""
    return is_admin(user) or (user.role == User.Role.DIRECTOR and user.pharmacy_id == pharmacy.id)


def check_can_write(user):
    if user.chat_muted:
        raise PermissionDenied("Вам запретили писать в этот чат.")


@extend_schema(tags=["chat"], parameters=[PHARMACY_PARAM])
class MessageViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """
    Чат аптеки. Сотрудник видит и пишет только в чат своей аптеки, админ проекта — в любой (?pharmacy=ID).
    Список — последние сообщения по возрастанию; ?after=ID — только новые (догрузка после обрыва связи).
    Новое сообщение сразу уходит всем, у кого чат открыт по WebSocket (ws/chat/); изменения — событием update.
    Изменить — только своё; удалить — своё, а директор и админ — любое; закрепить — директор и админ.
    """

    queryset = Message.objects.select_related("user", "reply_to__user").prefetch_related("reactions")
    serializer_class = MessageSerializer
    pagination_class = None
    filter_backends = []
    http_method_names = ["get", "post", "patch", "delete"]

    def pharmacy(self):
        if not hasattr(self, "_pharmacy"):
            params = self.request.query_params
            self._pharmacy = chat_pharmacy(self.request.user, params.get("pharmacy") or self.request.data.get("pharmacy"))
        return self._pharmacy

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Message.objects.none()
        return super().get_queryset().filter(pharmacy=self.pharmacy())

    @extend_schema(parameters=[OpenApiParameter("after", OpenApiTypes.INT, description="только сообщения с id больше этого")])
    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        after = request.query_params.get("after")
        if after and after.isdigit():
            queryset = queryset.filter(id__gt=after)
        else:
            last_ids = queryset.order_by("-id").values_list("id", flat=True)[:HISTORY]
            queryset = queryset.filter(id__in=list(last_ids))
        return Response(self.get_serializer(queryset, many=True).data)

    def perform_create(self, serializer):
        check_can_write(self.request.user)
        reply_to = serializer.validated_data.get("reply_to")
        if reply_to is not None and (reply_to.pharmacy_id != self.pharmacy().id or reply_to.deleted):
            raise ValidationError({"reply_to": "Нельзя ответить на это сообщение."})
        message = serializer.save(pharmacy=self.pharmacy(), user=self.request.user)
        broadcast(message.pharmacy_id, serializer.data)
        mark_read(self.request.user, message.pharmacy, message.id)  # своё (и всё до него) автор уже видел

    def updated(self, message):
        """Свежие данные сообщения — в ответ и всем по WebSocket."""
        data = self.get_serializer(self.get_queryset().get(pk=message.pk)).data
        broadcast_update(message.pharmacy_id, data)
        return Response(data)

    @extend_schema(summary="Изменить своё сообщение", request=EditSerializer, responses=MessageSerializer)
    def partial_update(self, request, *args, **kwargs):
        message = self.get_object()
        if message.user_id != request.user.id:
            raise PermissionDenied("Изменить можно только своё сообщение.")
        check_can_write(request.user)
        if message.deleted:
            raise ValidationError({"detail": "Сообщение удалено."})
        data = EditSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        text = data.validated_data["text"]
        if not text and not message.file:
            raise ValidationError({"text": "Пустое сообщение."})
        if text != message.text:
            message.text = text
            message.edited_at = timezone.now()
            message.save(update_fields=["text", "edited_at"])
        return self.updated(message)

    @extend_schema(summary="Удалить сообщение (у всех)", responses=MessageSerializer)
    def destroy(self, request, *args, **kwargs):
        message = self.get_object()
        if message.user_id != request.user.id and not can_moderate(request.user, message.pharmacy):
            raise PermissionDenied("Удалить чужое сообщение может только директор аптеки.")
        if not message.deleted:
            with transaction.atomic():
                if message.file:
                    message.file.delete(save=False)
                message.reactions.all().delete()
                was_pinned = message.pinned_at is not None
                message.text, message.duration, message.deleted, message.pinned_at = "", None, True, None
                message.kind = Message.Kind.TEXT
                message.save(update_fields=["text", "file", "duration", "deleted", "pinned_at", "kind"])
            if was_pinned:
                broadcast_pin(message.pharmacy_id, None)
        return self.updated(message)

    @extend_schema(summary="Реакция: тот же эмодзи — снять, другой — заменить", request=ReactSerializer, responses=MessageSerializer)
    @action(detail=True, methods=["post"])
    def react(self, request, pk=None):
        message = self.get_object()
        check_can_write(request.user)
        if message.deleted:
            raise ValidationError({"detail": "Сообщение удалено."})
        data = ReactSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        emoji = data.validated_data["emoji"]
        current = Reaction.objects.filter(message=message, user=request.user).first()
        if current and current.emoji == emoji:
            current.delete()
        else:
            Reaction.objects.update_or_create(message=message, user=request.user, defaults={"emoji": emoji})
        return self.updated(message)

    @extend_schema(summary="Закрепить / открепить (директор и админ)", request=None, responses=MessageSerializer)
    @action(detail=True, methods=["post"])
    def pin(self, request, pk=None):
        message = self.get_object()
        if not can_moderate(request.user, message.pharmacy):
            raise PermissionDenied("Закреплять может только директор аптеки.")
        if message.deleted:
            raise ValidationError({"detail": "Сообщение удалено."})
        pinning = message.pinned_at is None
        with transaction.atomic():
            # закреплённое одно: прежнее открепляется
            previous = list(self.get_queryset().filter(pinned_at__isnull=False).exclude(pk=message.pk))
            Message.objects.filter(pk__in=[m.pk for m in previous]).update(pinned_at=None)
            message.pinned_at = timezone.now() if pinning else None
            message.save(update_fields=["pinned_at"])
        for old in previous:
            self.updated(old)
        response = self.updated(message)
        broadcast_pin(message.pharmacy_id, response.data if pinning else None)
        return response

    @extend_schema(summary="Закреплённое сообщение аптеки (или null)", responses=MessageSerializer)
    @action(detail=False, methods=["get"])
    def pinned(self, request):
        message = self.get_queryset().filter(pinned_at__isnull=False).first()
        return Response(self.get_serializer(message).data if message else None)

    @extend_schema(summary="Отметить прочитанным до last_id", request=ReadSerializer, responses=ReadStateSerializer)
    @action(detail=False, methods=["post"])
    def read(self, request):
        data = ReadSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return Response({"read_up_to": mark_read(request.user, self.pharmacy(), data.validated_data["last_id"])})

    # ---------------------------------------------------------------- управление чатом

    @extend_schema(summary="Участники чата: сотрудники аптеки (аватар, роль, запрет писать, до чего дочитал)", responses=MemberSerializer(many=True))
    @action(detail=False, methods=["get"])
    def members(self, request):
        pharmacy = self.pharmacy()
        users = User.objects.filter(pharmacy=pharmacy, is_active=True).order_by("role", "full_name", "username")
        reads = dict(ChatRead.objects.filter(pharmacy=pharmacy).values_list("user_id", "last_read_id"))
        return Response(MemberSerializer(users, many=True, context={"reads": reads}).data)

    @extend_schema(summary="Запретить / разрешить сотруднику писать в чат (директор и админ)", request=MuteSerializer, responses=MemberSerializer)
    @action(detail=False, methods=["post"], url_path=r"members/(?P<user_id>\d+)/mute")
    def mute(self, request, user_id=None):
        pharmacy = self.pharmacy()
        if not can_moderate(request.user, pharmacy):
            raise PermissionDenied("Запрещать писать может только директор аптеки.")
        target = User.objects.filter(pk=user_id, pharmacy=pharmacy).first()
        if target is None:
            raise ValidationError({"detail": "Такого сотрудника в аптеке нет."})
        if target == request.user:
            raise ValidationError({"detail": "Себе запретить нельзя."})
        if target.role == User.Role.DIRECTOR and not is_admin(request.user):
            raise PermissionDenied("Директору запретить писать может только админ проекта.")
        data = MuteSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        target.chat_muted = data.validated_data["muted"]
        target.save(update_fields=["chat_muted"])
        broadcast_mute(pharmacy.id, target.id, target.chat_muted)
        reads = dict(ChatRead.objects.filter(pharmacy=pharmacy, user=target).values_list("user_id", "last_read_id"))
        return Response(MemberSerializer(target, context={"reads": reads}).data)

    @extend_schema(summary="Очистить историю чата аптеки (только админ проекта)", request=None, responses=None)
    @action(detail=False, methods=["post"])
    def clear(self, request):
        if not is_admin(request.user):
            raise PermissionDenied("Очистить чат может только админ проекта.")
        pharmacy = self.pharmacy()
        with transaction.atomic():
            for message in Message.objects.filter(pharmacy=pharmacy).exclude(file=""):
                message.file.delete(save=False)
            Message.objects.filter(pharmacy=pharmacy).delete()
            ChatRead.objects.filter(pharmacy=pharmacy).delete()
        broadcast_cleared(pharmacy.id)
        return Response(status=204)

    @extend_schema(summary="Все чаты аптек: участники, сообщения, последнее (только админ проекта)", parameters=[], responses=OverviewSerializer(many=True))
    @action(detail=False, methods=["get"])
    def overview(self, request):
        if not is_admin(request.user):
            raise PermissionDenied("Только для админа проекта.")
        pharmacies = Pharmacy.objects.annotate(
            members=Count("users", filter=Q(users__is_active=True), distinct=True),
            muted=Count("users", filter=Q(users__is_active=True, users__chat_muted=True), distinct=True),
            message_count=Count("messages", filter=Q(messages__deleted=False), distinct=True),
            last_id=Max("messages__id"),
        ).order_by("-last_id", "name")
        last = {m.id: m for m in Message.objects.filter(id__in=[p.last_id for p in pharmacies if p.last_id]).select_related("user")}
        rows = []
        for p in pharmacies:
            m = last.get(p.last_id)
            rows.append(
                {
                    "pharmacy": p.id,
                    "name": p.name,
                    "avatar_url": p.chat_avatar.url if p.chat_avatar else None,
                    "members": p.members,
                    "muted": p.muted,
                    "messages": p.message_count,
                    "last": m
                    and {
                        "author": MessageSerializer.author_of(m),
                        "text": "" if m.deleted else m.text[:120],
                        "kind": m.kind,
                        "deleted": m.deleted,
                        "created_at": m.created_at,
                    },
                }
            )
        return Response(rows)

    # ---------------------------------------------------------------- сведения о чате: фото, счётчики, медиа

    def media_queryset(self, kind):
        """Сообщения-вложения одного типа (link — сообщения со ссылками), новые первыми."""
        queryset = self.get_queryset().filter(deleted=False)
        if kind == "link":
            return queryset.filter(Q(text__icontains="http://") | Q(text__icontains="https://"))
        return queryset.filter(kind=kind)

    @extend_schema(summary="Сведения о чате: фото, участники, сколько фото / видео / голосовых / ссылок", responses=ChatInfoSerializer)
    @action(detail=False, methods=["get"])
    def info(self, request):
        pharmacy = self.pharmacy()
        return Response(
            {
                "pharmacy": pharmacy.id,
                "name": pharmacy.name,
                "avatar_url": pharmacy.chat_avatar.url if pharmacy.chat_avatar else None,
                "members": User.objects.filter(pharmacy=pharmacy, is_active=True).count(),
                "counts": {kind: self.media_queryset(kind).count() for kind in ("image", "video", "voice", "link")},
            }
        )

    @extend_schema(
        summary="Медиа чата: ?kind=image,video (через запятую) | voice | link; новые первыми",
        parameters=[OpenApiParameter("kind", OpenApiTypes.STR, description="image, video, voice, link — можно несколько через запятую")],
        responses=MediaItemSerializer(many=True),
    )
    @action(detail=False, methods=["get"])
    def media(self, request):
        kinds = [k for k in request.query_params.get("kind", "image,video").split(",") if k in ("image", "video", "voice", "link")]
        if not kinds:
            raise ValidationError({"kind": "Допустимо: image, video, voice, link."})
        queryset = self.media_queryset(kinds[0])
        for kind in kinds[1:]:
            queryset = queryset | self.media_queryset(kind)
        items = queryset.select_related("user").order_by("-id")[:500]
        return Response(
            [
                {
                    "id": m.id,
                    "kind": m.kind,
                    "file_url": m.file.url if m.file else None,
                    "duration": m.duration,
                    "text": m.text,
                    "author": MessageSerializer.author_of(m),
                    "created_at": m.created_at,
                }
                for m in items
            ]
        )

    @extend_schema(summary="Фото чата: POST — загрузить (file), DELETE — убрать (директор и админ)", request={"multipart/form-data": AvatarUploadSerializer}, responses=ChatInfoSerializer)
    @action(detail=False, methods=["post", "delete"], parser_classes=[MultiPartParser, FormParser, JSONParser])
    def avatar(self, request):
        pharmacy = self.pharmacy()
        if not can_moderate(request.user, pharmacy):
            raise PermissionDenied("Фото чата меняют директор аптеки и админ.")
        if request.method == "DELETE":
            delete_avatar(pharmacy, "chat_avatar")
        else:
            save_avatar(pharmacy, request.FILES.get("file"), "chat_avatar")
        broadcast_info(pharmacy)
        return self.info(request)
