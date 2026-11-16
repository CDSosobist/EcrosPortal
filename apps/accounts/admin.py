"""Админка Django: полный доступ к справочникам портала."""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import AdGroup, AdSyncRun, AuditEvent, Department, Employee, Role


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "parent", "head", "is_active", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("name", "code", "ad_dn")
    autocomplete_fields = ("parent", "head")
    list_select_related = ("parent", "head")


@admin.register(Employee)
class EmployeeAdmin(BaseUserAdmin):
    list_display = ("username", "full_name", "job_title", "department", "status", "is_active", "source")
    list_filter = ("status", "is_active", "source", "department", "missing_in_ad")
    search_fields = ("username", "last_name", "first_name", "email", "employee_no")
    autocomplete_fields = ("department", "manager")
    ordering = ("last_name", "first_name")
    readonly_fields = ("ad_dn", "ad_guid", "ad_synced_at", "ad_when_changed", "created_at", "updated_at", "last_login")
    fieldsets = (
        (None, {"fields": ("username", "password", "email", "is_active", "is_staff", "is_superuser")}),
        ("Персональные данные", {
            "fields": ("last_name", "first_name", "middle_name", "display_name", "job_title", "employee_no")
        }),
        ("Оргструктура", {"fields": ("department", "manager", "status", "hire_date", "dismissal_date")}),
        ("Контакты", {"fields": ("phone_internal", "phone_mobile", "office_room", "city")}),
        ("Синхронизация", {
            "fields": ("source", "ad_dn", "ad_guid", "ad_synced_at", "ad_when_changed", "missing_in_ad")
        }),
        ("Служебное", {"fields": ("last_login", "last_login_ip", "created_at", "updated_at")}),
    )
    filter_horizontal = ("groups", "user_permissions")

    @admin.display(description="ФИО")
    def full_name(self, obj: Employee) -> str:
        return obj.full_name


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_system", "is_active", "member_count", "head")
    list_filter = ("is_active", "is_system")
    search_fields = ("name", "code")
    filter_horizontal = ("members",)
    autocomplete_fields = ("head",)

    @admin.display(description="Сотрудников")
    def member_count(self, obj: Role) -> int:
        return obj.members.count()


@admin.register(AdSyncRun)
class AdSyncRunAdmin(admin.ModelAdmin):
    list_display = ("started_at", "provider", "mode", "status", "short_message", "triggered_by")
    list_filter = ("status", "mode", "provider")
    readonly_fields = ("started_at", "finished_at", "counters", "log_excerpt")

    @admin.display(description="Итог")
    def short_message(self, obj: AdSyncRun) -> str:
        return (obj.message or "")[:80]


@admin.register(AdGroup)
class AdGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "member_count", "seen_at")
    search_fields = ("name", "dn")


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "actor_name", "target_label", "ip_address")
    list_filter = ("action",)
    search_fields = ("actor_name", "target_label", "action")
    date_hierarchy = "created_at"
    readonly_fields = tuple(f.name for f in AuditEvent._meta.fields)

    def has_add_permission(self, request):  # noqa: D102
        return False

    def has_change_permission(self, request, obj=None):  # noqa: D102
        return False
