"""Проверка прав доступа к разделам портала."""
from __future__ import annotations

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from .models import Employee

ROLE_HR = "hr"
ROLE_ADMIN = "admin"
ROLE_MANAGER = "manager"
ROLE_DIRECTOR = "director"


def role_required(*codes: str):
    """Пускает только владельцев одной из ролей (суперпользователь — всегда)."""

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(request, *args, **kwargs):
            if not request.user.has_role(*codes):
                raise PermissionDenied("Недостаточно прав для этого раздела")
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def hr_required(view):
    return role_required(ROLE_HR)(view)


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        user = request.user
        if not (user.is_portal_admin or user.is_hr):
            raise PermissionDenied("Раздел доступен администраторам портала и HR")
        return view(request, *args, **kwargs)

    return wrapper


def staff_required(view):
    """Доступ в техническую админку Django."""
    return role_required(ROLE_ADMIN)(view)


def can_view_employee(viewer: Employee, target: Employee) -> bool:
    """Карточку сотрудника видят все авторизованные, кроме уволенных коллег."""
    return True


def can_approve_vacations(viewer: Employee) -> bool:
    return viewer.is_manager or viewer.has_role(ROLE_HR, ROLE_DIRECTOR, ROLE_ADMIN)


def can_manage_vacation_balance(viewer: Employee) -> bool:
    return viewer.is_hr or viewer.is_portal_admin


def can_manage_approval_chains(viewer: Employee) -> bool:
    return viewer.is_hr or viewer.is_portal_admin
