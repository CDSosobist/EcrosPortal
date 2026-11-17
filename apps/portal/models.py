"""Модели портала: новости, база знаний, заявки, уведомления, ссылки."""
from __future__ import annotations

import uuid
from pathlib import Path

from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from apps.accounts.models import Employee
from apps.accounts.utils import translit

from .storage import attachment_storage


class Notification(models.Model):
    """Внутреннее уведомление сотрудника."""

    class Kind(models.TextChoices):
        VACATION = "vacation", "Отпуск"
        APPROVAL = "approval", "Согласование"
        TICKET = "ticket", "Заявка"
        NEWS = "news", "Новости"
        SYSTEM = "system", "Система"

    recipient = models.ForeignKey(
        Employee, verbose_name="Получатель", on_delete=models.CASCADE, related_name="notifications"
    )
    actor = models.ForeignKey(
        Employee,
        verbose_name="Инициатор",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sent_notifications",
    )
    kind = models.CharField("Тип", max_length=20, choices=Kind.choices, default=Kind.SYSTEM)
    title = models.CharField("Заголовок", max_length=255)
    body = models.TextField("Текст", blank=True)
    url = models.CharField("Ссылка", max_length=500, blank=True)
    is_read = models.BooleanField("Прочитано", default=False, db_index=True)
    created_at = models.DateTimeField("Создано", default=timezone.now, db_index=True)

    class Meta:
        verbose_name = "Уведомление"
        verbose_name_plural = "Уведомления"
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["recipient", "is_read"])]

    def __str__(self) -> str:
        return f"{self.recipient.short_name}: {self.title}"

    @classmethod
    def push(
        cls,
        recipient: Employee | None,
        *,
        title: str,
        body: str = "",
        url: str = "",
        kind: str = Kind.SYSTEM,
        actor: Employee | None = None,
    ) -> "Notification | None":
        if recipient is None or not getattr(recipient, "pk", None):
            return None
        return cls.objects.create(
            recipient=recipient,
            actor=actor,
            kind=kind,
            title=title[:255],
            body=body,
            url=url[:500],
        )


class NewsCategory(models.Model):
    name = models.CharField("Название", max_length=80, unique=True)
    code = models.SlugField("Код", max_length=80, unique=True)
    color = models.CharField("Цвет", max_length=20, default="#2563eb")
    sort_order = models.PositiveSmallIntegerField("Порядок", default=100)

    class Meta:
        verbose_name = "Категория новостей"
        verbose_name_plural = "Категории новостей"
        ordering = ("sort_order", "name")

    def __str__(self) -> str:
        return self.name


class NewsPost(models.Model):
    """Новость или объявление на главной странице."""

    title = models.CharField("Заголовок", max_length=255)
    slug = models.SlugField("Слаг", max_length=255, unique=True, blank=True)
    category = models.ForeignKey(
        NewsCategory,
        verbose_name="Категория",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="posts",
    )
    summary = models.CharField("Краткое описание", max_length=400, blank=True)
    body = models.TextField("Текст")
    author = models.ForeignKey(
        Employee,
        verbose_name="Автор",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="news_posts",
    )
    is_pinned = models.BooleanField("Закреплено", default=False)
    is_published = models.BooleanField("Опубликовано", default=True)
    published_at = models.DateTimeField("Дата публикации", default=timezone.now)
    views_count = models.PositiveIntegerField("Просмотров", default=0)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Новость"
        verbose_name_plural = "Новости"
        ordering = ("-is_pinned", "-published_at")
        indexes = [models.Index(fields=["is_published", "published_at"])]

    def __str__(self) -> str:
        return self.title

    def get_absolute_url(self) -> str:
        return reverse("portal:news_detail", args=[self.slug])

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._unique_slug()
        super().save(*args, **kwargs)

    def _unique_slug(self) -> str:
        base = slugify(translit(self.title))[:200] or f"news-{timezone.now():%Y%m%d%H%M%S}"
        candidate = base
        index = 1
        while NewsPost.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
            index += 1
            candidate = f"{base}-{index}"
        return candidate

    @property
    def short_text(self) -> str:
        return self.summary or (self.body[:180] + "…" if len(self.body) > 180 else self.body)


class KbCategory(models.Model):
    name = models.CharField("Название", max_length=120, unique=True)
    code = models.SlugField("Код", max_length=120, unique=True)
    description = models.CharField("Описание", max_length=255, blank=True)
    icon = models.CharField("Иконка", max_length=40, default="book")
    sort_order = models.PositiveSmallIntegerField("Порядок", default=100)

    class Meta:
        verbose_name = "Раздел базы знаний"
        verbose_name_plural = "Разделы базы знаний"
        ordering = ("sort_order", "name")

    def __str__(self) -> str:
        return self.name

    @property
    def published_count(self) -> int:
        return self.articles.filter(is_published=True).count()


class KnowledgeArticle(models.Model):
    """Инструкция, регламент или памятка."""

    title = models.CharField("Заголовок", max_length=255)
    slug = models.SlugField("Слаг", max_length=255, unique=True, blank=True)
    category = models.ForeignKey(
        KbCategory,
        verbose_name="Раздел",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="articles",
    )
    summary = models.CharField("Краткое описание", max_length=400, blank=True)
    body = models.TextField("Текст")
    tags = models.CharField("Теги (через запятую)", max_length=255, blank=True)
    author = models.ForeignKey(
        Employee,
        verbose_name="Автор",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="kb_articles",
    )
    is_published = models.BooleanField("Опубликована", default=True)
    views_count = models.PositiveIntegerField("Просмотров", default=0)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Статья базы знаний"
        verbose_name_plural = "База знаний"
        ordering = ("-updated_at",)
        indexes = [models.Index(fields=["is_published", "category"])]

    def __str__(self) -> str:
        return self.title

    def get_absolute_url(self) -> str:
        return reverse("portal:kb_detail", args=[self.slug])

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(translit(self.title))[:200] or f"article-{timezone.now():%Y%m%d%H%M%S}"
            candidate = base
            index = 1
            while KnowledgeArticle.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                index += 1
                candidate = f"{base}-{index}"
            self.slug = candidate
        super().save(*args, **kwargs)

    @property
    def tag_list(self) -> list[str]:
        return [t.strip() for t in self.tags.split(",") if t.strip()]


class ServiceTicket(models.Model):
    """Заявка в службу поддержки (ИТ, кадры, АХО)."""

    class Category(models.TextChoices):
        IT = "it", "ИТ и оборудование"
        ACCESS = "access", "Доступы и учётные записи"
        HR = "hr", "Кадры и документы"
        OFFICE = "office", "Офис и АХО"
        OTHER = "other", "Другое"

    class Priority(models.TextChoices):
        LOW = "low", "Низкий"
        NORMAL = "normal", "Обычный"
        HIGH = "high", "Высокий"
        CRITICAL = "critical", "Критичный"

    class Status(models.TextChoices):
        NEW = "new", "Новая"
        IN_PROGRESS = "in_progress", "В работе"
        WAITING = "waiting", "Ждёт ответа"
        RESOLVED = "resolved", "Решена"
        CLOSED = "closed", "Закрыта"

    subject = models.CharField("Тема", max_length=255)
    description = models.TextField("Описание")
    category = models.CharField("Категория", max_length=20, choices=Category.choices, default=Category.IT)
    priority = models.CharField(
        "Приоритет", max_length=20, choices=Priority.choices, default=Priority.NORMAL
    )
    status = models.CharField(
        "Статус", max_length=20, choices=Status.choices, default=Status.NEW, db_index=True
    )
    requester = models.ForeignKey(
        Employee,
        verbose_name="Заявитель",
        on_delete=models.CASCADE,
        related_name="tickets_created",
    )
    assignee = models.ForeignKey(
        Employee,
        verbose_name="Исполнитель",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tickets_assigned",
    )
    created_at = models.DateTimeField("Создана", default=timezone.now, db_index=True)
    updated_at = models.DateTimeField("Обновлена", auto_now=True)
    closed_at = models.DateTimeField("Закрыта", null=True, blank=True)

    class Meta:
        verbose_name = "Заявка"
        verbose_name_plural = "Заявки"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"№{self.pk} {self.subject}"

    def get_absolute_url(self) -> str:
        return reverse("portal:ticket_detail", args=[self.pk])

    @property
    def number(self) -> str:
        return f"SD-{self.pk:05d}" if self.pk else "—"

    @property
    def is_open(self) -> bool:
        return self.status not in {self.Status.RESOLVED, self.Status.CLOSED}

    @property
    def priority_class(self) -> str:
        return {
            self.Priority.LOW: "muted",
            self.Priority.NORMAL: "info",
            self.Priority.HIGH: "warn",
            self.Priority.CRITICAL: "danger",
        }.get(self.priority, "info")


class TicketComment(models.Model):
    ticket = models.ForeignKey(
        ServiceTicket, verbose_name="Заявка", on_delete=models.CASCADE, related_name="comments"
    )
    author = models.ForeignKey(
        Employee, verbose_name="Автор", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="ticket_comments",
    )
    body = models.TextField("Текст")
    is_internal = models.BooleanField("Внутренний комментарий", default=False)
    created_at = models.DateTimeField("Создан", default=timezone.now)

    class Meta:
        verbose_name = "Комментарий к заявке"
        verbose_name_plural = "Комментарии к заявкам"
        ordering = ("created_at",)

    def __str__(self) -> str:
        return f"{self.ticket.number}: {self.body[:40]}"


def attachment_upload_to(instance: "Attachment", filename: str) -> str:
    """Случайное имя файла на диске: оригинал хранится в базе.

    Так имена не предсказуемы, а исходное название не попадает в путь
    (защита от обхода каталогов и «красивых» ссылок на чужие файлы).
    """
    suffix = Path(filename or "").suffix.lower()
    suffix = suffix[:12] if suffix.isascii() and suffix.replace(".", "").isalnum() else ""
    return f"tickets/{instance.ticket_id}/{uuid.uuid4().hex}{suffix}"


class Attachment(models.Model):
    """Файл, приложенный к заявке или к комментарию в ней."""

    ticket = models.ForeignKey(
        ServiceTicket, verbose_name="Заявка", on_delete=models.CASCADE, related_name="attachments"
    )
    comment = models.ForeignKey(
        TicketComment,
        verbose_name="Комментарий",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    file = models.FileField("Файл", storage=attachment_storage, upload_to=attachment_upload_to, max_length=255)
    original_name = models.CharField("Имя файла", max_length=255)
    content_type = models.CharField("Тип содержимого", max_length=120, blank=True)
    size = models.PositiveBigIntegerField("Размер, байт", default=0)
    is_internal = models.BooleanField(
        "Видно только исполнителям", default=False,
        help_text="Включается автоматически для файлов во внутренних комментариях.",
    )
    uploaded_by = models.ForeignKey(
        Employee,
        verbose_name="Загрузил",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ticket_attachments",
    )
    created_at = models.DateTimeField("Загружен", default=timezone.now)

    class Meta:
        verbose_name = "Вложение заявки"
        verbose_name_plural = "Вложения заявок"
        ordering = ("created_at", "id")
        indexes = [models.Index(fields=["ticket", "comment"])]

    def __str__(self) -> str:
        return self.original_name

    @property
    def download_url(self) -> str:
        return reverse("portal:attachment_download", args=[self.pk])

    @property
    def extension(self) -> str:
        return Path(self.original_name).suffix.lower().lstrip(".")

    @property
    def is_image(self) -> bool:
        return self.content_type in {"image/png", "image/jpeg", "image/gif", "image/webp"}

    @property
    def icon(self) -> str:
        """Имя иконки из SVG-спрайта для списка вложений."""
        if self.is_image:
            return "image"
        if self.extension in {"xls", "xlsx", "ods", "csv"}:
            return "table"
        if self.extension in {"doc", "docx", "odt", "rtf", "txt", "pdf", "log"}:
            return "file"
        if self.extension in {"zip", "rar", "7z", "tar", "gz"}:
            return "database"
        if self.extension in {"eml", "msg"}:
            return "mail"
        return "paperclip"

class QuickLink(models.Model):
    """Плитка полезной ссылки на дашборде."""

    title = models.CharField("Название", max_length=120)
    url = models.CharField("Ссылка", max_length=500)
    icon = models.CharField("Иконка", max_length=40, default="link")
    description = models.CharField("Описание", max_length=255, blank=True)
    sort_order = models.PositiveSmallIntegerField("Порядок", default=100)
    is_active = models.BooleanField("Активна", default=True)
    open_in_new_tab = models.BooleanField("Открывать в новой вкладке", default=True)

    class Meta:
        verbose_name = "Быстрая ссылка"
        verbose_name_plural = "Быстрые ссылки"
        ordering = ("sort_order", "title")

    def __str__(self) -> str:
        return self.title
