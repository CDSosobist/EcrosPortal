"""Хранилище вложений заявок.

Вложения лежат в отдельном каталоге (``PORTAL_ATTACHMENTS_ROOT``), а не в
``MEDIA_ROOT``: иначе nginx или отладочная раздача медиа отдавали бы их по
прямой ссылке любому, кто угадает имя файла. Доступ — только через view
``portal:attachment_download`` с проверкой прав, поэтому ``url()`` намеренно
запрещён: случайное использование должно падать, а не выдавать публичный адрес.
"""
from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class AttachmentStorage(FileSystemStorage):
    """Файловое хранилище вне MEDIA_ROOT с ленивым определением каталога."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("base_url", None)
        super().__init__(*args, **kwargs)

    @property
    def base_location(self) -> str:  # noqa: D102 - переопределяем cached_property
        root = Path(settings.PORTAL_ATTACHMENTS_ROOT)
        root.mkdir(parents=True, exist_ok=True)
        return str(root)

    @property
    def location(self) -> str:  # noqa: D102
        return os.path.abspath(self.base_location)

    @property
    def base_url(self) -> None:  # noqa: D102 - публичных URL у вложений нет
        return None

    def url(self, name):  # noqa: D102
        raise ValueError(
            "Вложения заявок не отдаются по прямой ссылке — используйте "
            "portal:attachment_download (см. Attachment.download_url)."
        )

    def deconstruct(self):  # noqa: D102 - путь хранилища не должен попадать в миграции
        return ("apps.portal.storage.AttachmentStorage", [], {})


attachment_storage = AttachmentStorage()
