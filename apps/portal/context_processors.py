"""Общий контекст шаблонов: счётчики, настройки портала, активный период."""
from __future__ import annotations

from django.conf import settings


def portal_context(request):
    context = {
        "PORTAL_SITE_NAME": settings.PORTAL_SITE_NAME,
        "PORTAL_COMPANY_NAME": settings.PORTAL_COMPANY_NAME,
        "PORTAL_SUPPORT_EMAIL": settings.PORTAL_SUPPORT_EMAIL,
        "unread_notifications": 0,
        "pending_approvals": 0,
        "current_schedule_period": None,
        "current_path": request.path,
    }
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return context

    from apps.portal.models import Notification

    context["unread_notifications"] = Notification.objects.filter(
        recipient=user, is_read=False
    ).count()

    # Флаги ролей считаем один раз на запрос: в шаблонах они используются часто.
    try:
        role_codes = user.role_codes
    except Exception:  # noqa: BLE001
        role_codes = set()
    context["is_hr"] = user.is_superuser or "hr" in role_codes
    context["is_portal_admin"] = (
        user.is_superuser or user.is_staff or "admin" in role_codes
    )
    context["is_director"] = "director" in role_codes
    context["can_approve_vacations"] = bool(
        user.is_manager or {"hr", "director", "admin"} & role_codes or user.is_superuser
    )

    from apps.vacations import services as vacation_services

    try:
        context["pending_approvals"] = vacation_services.pending_steps_for(user).count()
    except Exception:  # noqa: BLE001 - контекст не должен ронять страницу
        context["pending_approvals"] = 0
    try:
        context["current_schedule_period"] = vacation_services.current_period()
    except Exception:  # noqa: BLE001
        context["current_schedule_period"] = None

    context["nav_departments"] = []
    if user.department_id:
        context["nav_departments"] = [d.name for d in user.department.get_ancestors()[-2:]]
    return context
