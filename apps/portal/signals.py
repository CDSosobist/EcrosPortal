"""Сигналы портала: уборка файлов вложений.

Каскадное удаление (при удалении заявки или комментария) не вызывает
``Attachment.delete()``, поэтому файлы удаляются через ``post_delete`` —
он срабатывает и при одиночном удалении, и при массовом.
"""
from __future__ import annotations

import logging

from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Attachment
from .storage import attachment_storage

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=Attachment, dispatch_uid="portal.attachment_delete_file")
def delete_attachment_file(sender, instance: Attachment, **kwargs) -> None:
    stored_name = instance.file.name if instance.file else ""
    if not stored_name:
        return
    try:
        if attachment_storage.exists(stored_name):
            attachment_storage.delete(stored_name)
    except Exception:  # noqa: BLE001 - сбой уборки не должен мешать удалению записи
        logger.warning("Не удалось удалить файл вложения %s", stored_name, exc_info=True)
