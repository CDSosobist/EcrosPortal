"""Логика вложений заявок: проверка файлов и сохранение.

Правила намеренно жёсткие, потому что заявки — публичный для сотрудников канал:
  * белый список расширений (никаких исполняемых файлов, скриптов, HTML и SVG);
  * ограничение размера каждого файла и количества файлов за раз;
  * имя на диске генерируется случайно, оригинал хранится только в базе;
  * скачивание — через защищённую view, файлы не лежат в MEDIA_ROOT.
"""
from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Attachment

CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def normalize_filename(name: str) -> str:
    """Безопасное имя файла для показа пользователю (без пути и управляющих символов)."""
    cleaned = CONTROL_CHARS.sub("", Path(name or "файл").name).strip()
    cleaned = cleaned.replace("\\", "_").replace("/", "_")
    return (cleaned or "файл")[:255]


def attachment_extension(name: str) -> str:
    return Path(name or "").suffix.lower().lstrip(".")


def allowed_extensions() -> list[str]:
    return list(settings.PORTAL_ATTACHMENT_EXTENSIONS)


def max_size_bytes() -> int:
    return settings.PORTAL_ATTACHMENT_MAX_MB * 1024 * 1024


def validate_upload(uploaded) -> None:
    """Проверяет один загруженный файл. Бросает ValidationError с понятным текстом."""
    name = normalize_filename(uploaded.name)
    extension = attachment_extension(name)
    if not extension:
        raise ValidationError(
            f"Файл «{name}» без расширения. Разрешены: {', '.join(allowed_extensions())}."
        )
    if extension not in allowed_extensions():
        raise ValidationError(
            f"Формат «.{extension}» запрещён. Разрешены: {', '.join(allowed_extensions())}."
        )
    max_bytes = max_size_bytes()
    if uploaded.size and uploaded.size > max_bytes:
        raise ValidationError(
            f"Файл «{name}» больше {settings.PORTAL_ATTACHMENT_MAX_MB} МБ "
            f"({uploaded.size / 1024 / 1024:.1f} МБ)."
        )
    if not uploaded.size:
        raise ValidationError(f"Файл «{name}» пустой.")


def validate_batch(files) -> list[str]:
    """Проверяет набор файлов, возвращает список ошибок (пустой — всё хорошо)."""
    files = list(files or [])
    errors: list[str] = []
    if len(files) > settings.PORTAL_ATTACHMENT_MAX_FILES:
        errors.append(
            f"За раз можно приложить не более {settings.PORTAL_ATTACHMENT_MAX_FILES} файлов."
        )
    for uploaded in files:
        try:
            validate_upload(uploaded)
        except ValidationError as exc:
            errors.extend(exc.messages)
    return errors


@transaction.atomic
def save_attachments(files, *, ticket, comment=None, uploaded_by=None, is_internal: bool = False):
    """Сохраняет файлы и создаёт записи Attachment. Валидацию выполняет вызывающий код."""
    saved: list[Attachment] = []
    for uploaded in files or []:
        name = normalize_filename(uploaded.name)
        attachment = Attachment(
            ticket=ticket,
            comment=comment,
            original_name=name,
            content_type=(getattr(uploaded, "content_type", "") or "")[:120],
            size=uploaded.size or 0,
            is_internal=bool(is_internal),
            uploaded_by=uploaded_by if getattr(uploaded_by, "pk", None) else None,
        )
        attachment.file.save(name, uploaded, save=False)
        attachment.save()
        saved.append(attachment)
    return saved


def attachments_visible_to(queryset, *, is_agent: bool):
    """Скрывает внутренние вложения от заявителя."""
    if is_agent:
        return queryset
    return queryset.filter(is_internal=False)
