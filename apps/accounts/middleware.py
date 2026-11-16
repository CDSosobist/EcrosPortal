"""Сквозная проверка авторизации.

Внутренний портал не должен отдавать контент анонимам даже при забытом
декораторе, поэтому проверка вынесена в middleware.
"""
from __future__ import annotations

from django.http import HttpResponseRedirect
from django.urls import reverse

EXEMPT_PREFIXES = (
    "/login",
    "/logout",
    "/static/",
    "/media/",
    "/admin/",
    "/healthz",
    "/favicon.ico",
)


class RequireLoginMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        if (
            request.user.is_authenticated
            or any(path.startswith(prefix) for prefix in EXEMPT_PREFIXES)
        ):
            return self.get_response(request)

        login_url = reverse("accounts:login")
        if path not in ("/", ""):
            login_url = f"{login_url}?next={path}"
        return HttpResponseRedirect(login_url)
