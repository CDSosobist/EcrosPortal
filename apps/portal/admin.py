"""Админка контента портала."""
from django.contrib import admin

from .models import (
    KbCategory,
    KnowledgeArticle,
    NewsCategory,
    NewsPost,
    Notification,
    QuickLink,
    ServiceTicket,
    TicketComment,
)


@admin.register(NewsPost)
class NewsPostAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "author", "is_pinned", "is_published", "published_at", "views_count")
    list_filter = ("is_published", "is_pinned", "category")
    search_fields = ("title", "body")
    prepopulated_fields = {"slug": ("title",)}
    autocomplete_fields = ("author",)
    date_hierarchy = "published_at"


@admin.register(NewsCategory)
class NewsCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "sort_order")


@admin.register(KnowledgeArticle)
class KnowledgeArticleAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "author", "is_published", "updated_at", "views_count")
    list_filter = ("is_published", "category")
    search_fields = ("title", "body", "tags")
    prepopulated_fields = {"slug": ("title",)}
    autocomplete_fields = ("author",)


@admin.register(KbCategory)
class KbCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "sort_order")


class TicketCommentInline(admin.TabularInline):
    model = TicketComment
    extra = 0
    readonly_fields = ("created_at",)


@admin.register(ServiceTicket)
class ServiceTicketAdmin(admin.ModelAdmin):
    list_display = ("number", "subject", "category", "priority", "status", "requester", "assignee", "created_at")
    list_filter = ("status", "category", "priority")
    search_fields = ("subject", "description")
    autocomplete_fields = ("requester", "assignee")
    inlines = [TicketCommentInline]
    date_hierarchy = "created_at"


@admin.register(QuickLink)
class QuickLinkAdmin(admin.ModelAdmin):
    list_display = ("title", "url", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("created_at", "recipient", "kind", "title", "is_read")
    list_filter = ("kind", "is_read")
    search_fields = ("title", "recipient__last_name")
