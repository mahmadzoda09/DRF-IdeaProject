"""Аватарки: любая картинка пережимается в квадрат 640×640 JPEG — маленький файл, и под видом фото ничего чужого не пройдёт."""

import uuid
from io import BytesIO

from django.core.files.base import ContentFile
from PIL import Image, ImageOps
from rest_framework.exceptions import ValidationError

SIZE = 640  # хватает и для кружка, и чтобы по клику посмотреть крупно
MAX_UPLOAD_MB = 10


def save_avatar(owner, upload, field="avatar"):
    """Пережать фото и сохранить в owner.<field> (аватарка сотрудника или фото чата аптеки)."""
    if upload is None:
        raise ValidationError({"file": "Выберите фото."})
    if upload.size > MAX_UPLOAD_MB * 1024 * 1024:
        raise ValidationError({"file": f"Фото больше {MAX_UPLOAD_MB} МБ."})
    try:
        image = Image.open(upload)
        image.load()
    except Exception:  # не картинка, битый файл, слишком большое разрешение (DecompressionBomb)
        raise ValidationError({"file": "Это не фото: подойдут jpg, png, webp, gif."})
    image = ImageOps.exif_transpose(image).convert("RGB")  # фото с телефона — правильной стороной вверх
    image = ImageOps.fit(image, (SIZE, SIZE), Image.Resampling.LANCZOS)  # квадрат по центру
    out = BytesIO()
    image.save(out, "JPEG", quality=88, optimize=True)
    delete_avatar(owner, field, save=False)
    getattr(owner, field).save(f"{uuid.uuid4().hex}.jpg", ContentFile(out.getvalue()), save=False)
    owner.save(update_fields=[field])
    return owner


def delete_avatar(owner, field="avatar", save=True):
    if getattr(owner, field):
        getattr(owner, field).delete(save=False)
    if save:
        owner.save(update_fields=[field])
    return owner
