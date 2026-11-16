"""Админка раздела отпусков."""
from django.contrib import admin

from .models import (
    ApprovalChainStep,
    ApprovalChainTemplate,
    Holiday,
    SchedulePeriod,
    VacationBalance,
    VacationRequest,
    VacationRequestStep,
    VacationType,
)


class ApprovalChainStepInline(admin.TabularInline):
    model = ApprovalChainStep
    extra = 1
    fields = ("order", "title", "approver_kind", "role", "user", "skip_if_missing")
    autocomplete_fields = ("user",)


@admin.register(ApprovalChainTemplate)
class ApprovalChainTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "department", "is_default", "is_active", "priority", "step_count")
    list_filter = ("is_active", "is_default")
    search_fields = ("name", "code")
    filter_horizontal = ("applies_to_roles", "vacation_types")
    inlines = [ApprovalChainStepInline]

    @admin.display(description="Шагов")
    def step_count(self, obj: ApprovalChainTemplate) -> int:
        return obj.steps.count()


class VacationRequestStepInline(admin.TabularInline):
    model = VacationRequestStep
    extra = 0
    fields = ("order", "title", "approver", "status", "comment", "decided_at")
    readonly_fields = ("decided_at",)


@admin.register(VacationRequest)
class VacationRequestAdmin(admin.ModelAdmin):
    list_display = ("number", "employee", "vacation_type", "start_date", "end_date", "days_count", "status")
    list_filter = ("status", "period", "vacation_type")
    search_fields = ("employee__last_name", "employee__first_name", "employee__username")
    date_hierarchy = "start_date"
    autocomplete_fields = ("employee",)
    inlines = [VacationRequestStepInline]
    readonly_fields = ("days_count", "created_at", "updated_at", "submitted_at", "decided_at")

    @admin.display(description="Номер")
    def number(self, obj: VacationRequest) -> str:
        return obj.number


@admin.register(VacationBalance)
class VacationBalanceAdmin(admin.ModelAdmin):
    list_display = ("employee", "year", "entitled_days", "carried_over_days")
    list_filter = ("year",)
    search_fields = ("employee__last_name", "employee__first_name")
    autocomplete_fields = ("employee",)


@admin.register(VacationType)
class VacationTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "default_days", "consumes_balance", "requires_approval", "is_active")
    list_filter = ("is_active", "requires_approval", "consumes_balance")


@admin.register(SchedulePeriod)
class SchedulePeriodAdmin(admin.ModelAdmin):
    list_display = ("year", "status", "submission_deadline", "published_at", "published_by")
    list_filter = ("status",)


@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    list_display = ("date", "name", "is_working")
    list_filter = ("is_working",)
    search_fields = ("name",)
    date_hierarchy = "date"


@admin.register(VacationRequestStep)
class VacationRequestStepAdmin(admin.ModelAdmin):
    list_display = ("request", "order", "title", "approver", "status", "decided_at")
    list_filter = ("status",)
    search_fields = ("approver__last_name", "title")
