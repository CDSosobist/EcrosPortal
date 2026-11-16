"""Вход, выход и служебные страницы учётных записей."""
from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.core.cache import cache
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from apps.accounts.ad.factory import get_directory, provider_name
from apps.accounts.ad.base import AdError
from apps.accounts.ad.sync import AdSyncService
from apps.accounts.auth import get_last_ad_error
from apps.accounts.models import AdGroup, AdSyncRun, Employee, Role
from apps.accounts.permissions import admin_required


def _client_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def _throttle_key(request, username: str) -> str:
    return f"login-fail:{_client_ip(request)}:{username.lower()}"


def _is_locked(request, username: str) -> bool:
    return cache.get(_throttle_key(request, username), 0) >= settings.LOGIN_MAX_ATTEMPTS


def _register_failure(request, username: str) -> None:
    key = _throttle_key(request, username)
    attempts = cache.get(key, 0) + 1
    cache.set(key, attempts, settings.LOGIN_LOCKOUT_SECONDS)


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)

    next_url = request.POST.get("next") or request.GET.get("next") or ""
    if next_url and not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = ""

    error = ""
    username = ""
    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""
        if not username or not password:
            error = "Введите логин и пароль."
        elif _is_locked(request, username):
            error = (
                "Слишком много неудачных попыток входа. "
                "Повторите через 15 минут или обратитесь в ИТ-отдел."
            )
        else:
            user = authenticate(request, username=username, password=password)
            if user is not None:
                cache.delete(_throttle_key(request, username))
                login(request, user)
                if not request.POST.get("remember"):
                    request.session.set_expiry(0)
                return redirect(next_url or settings.LOGIN_REDIRECT_URL)
            _register_failure(request, username)
            error = get_last_ad_error() or "Неверный логин или пароль домена."

    context = {
        "error": error,
        "username": username,
        "next": next_url,
        "ad_provider": provider_name(),
        "ad_enabled": settings.AD.get("ENABLED"),
        "local_login_allowed": settings.ALLOW_LOCAL_PASSWORD_LOGIN,
    }
    return render(request, "accounts/login.html", context, status=400 if error else 200)


@require_http_methods(["GET", "POST"])
def logout_view(request):
    logout(request)
    messages.info(request, "Вы вышли из портала.")
    return redirect(reverse("accounts:login"))


def profile_view(request):
    employee = request.user
    subordinates = employee.subordinates.filter(is_active=True).select_related("department")[:20]
    return render(
        request,
        "accounts/profile.html",
        {
            "employee": employee,
            "subordinates": subordinates,
            "manager_chain": employee.manager_chain(),
            "roles": employee.roles.all(),
        },
    )


def healthz(request):
    """Простой health-check для мониторинга и балансировщика."""
    ok = True
    detail = {}
    try:
        Employee.objects.exists()
    except Exception as exc:  # noqa: BLE001
        ok = False
        detail["db"] = str(exc)
    return HttpResponse(
        ('{"status":"ok"}' if ok else '{"status":"error"}'),
        content_type="application/json",
        status=200 if ok else 503,
    )


@admin_required
def manage_sync(request):
    """Состояние интеграции с AD и ручной запуск синхронизации."""
    directory = None
    health = None
    try:
        directory = get_directory()
        health = directory.healthcheck()
    except AdError as exc:
        health = None
        messages.error(request, str(exc))
    except Exception as exc:  # noqa: BLE001
        health = None
        messages.error(request, f"Не удалось проверить каталог: {exc}")

    if request.method == "POST":
        dry_run = request.POST.get("dry_run") == "1"
        mode = AdSyncRun.Mode.DRY_RUN if dry_run else AdSyncRun.Mode.FULL
        service = AdSyncService(
            directory=get_directory(),
            actor=request.user,
            dry_run=dry_run,
            mode=mode,
            request=request,
        )
        run = service.run()
        if run.status == AdSyncRun.Status.FAILED:
            messages.error(request, f"Синхронизация не выполнена: {run.message}")
        elif dry_run:
            messages.success(
                request,
                "Проверка выполнена: изменения не сохранены. "
                f"Создано: {run.counters.get('created', 0)}, обновлено: {run.counters.get('updated', 0)}.",
            )
        else:
            messages.success(request, run.message or "Синхронизация завершена.")
        return redirect("accounts:manage_sync")

    runs = AdSyncRun.objects.select_related("triggered_by")[:15]
    return render(
        request,
        "accounts/manage_sync.html",
        {
            "health": health,
            "provider": provider_name(),
            "ad_settings": {
                "server": settings.AD.get("SERVER") or "не задан",
                "port": settings.AD.get("PORT"),
                "ssl": settings.AD.get("USE_SSL"),
                "base_dn": settings.AD.get("BASE_DN") or "не задан",
                "user_filter": settings.AD.get("USER_FILTER"),
                "bind_dn": settings.AD.get("BIND_DN") or "не задан",
                "sync_interval": settings.AD.get("SYNC_INTERVAL_MINUTES"),
            },
            "runs": runs,
            "last_run": runs[0] if runs else None,
            "stats": {
                "employees": Employee.objects.count(),
                "active": Employee.objects.filter(is_active=True).count(),
                "missing_in_ad": Employee.objects.filter(missing_in_ad=True).count(),
                "departments": Employee.objects.exclude(department=None).count(),
            },
            "ad_groups": AdGroup.objects.order_by("-member_count")[:60],
        },
    )


@admin_required
def manage_roles(request):
    """Сопоставление портальных ролей с группами Active Directory."""
    if request.method == "POST":
        role_id = request.POST.get("role_id")
        role = Role.objects.filter(pk=role_id).first()
        if role is not None:
            raw = request.POST.get("ad_groups", "")
            patterns = [line.strip() for line in raw.splitlines() if line.strip()]
            role.ad_groups = patterns
            head_id = request.POST.get("head") or ""
            role.head = Employee.objects.filter(pk=head_id).first() if head_id else None
            role.save(update_fields=["ad_groups", "head"])
            messages.success(request, f"Роль «{role.name}» обновлена.")
        return redirect("accounts:manage_roles")

    roles = Role.objects.prefetch_related("members", "head").order_by("sort_order", "name")
    return render(
        request,
        "accounts/manage_roles.html",
        {
            "roles": roles,
            "employees": Employee.objects.filter(is_active=True).order_by("last_name")[:500],
            "ad_groups": AdGroup.objects.order_by("name"),
        },
    )
