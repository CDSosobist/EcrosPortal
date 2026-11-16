"""Маршруты учётных записей и служебных разделов."""
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("profile/", views.profile_view, name="profile"),
    path("healthz/", views.healthz, name="healthz"),
    path("manage/sync/", views.manage_sync, name="manage_sync"),
    path("manage/roles/", views.manage_roles, name="manage_roles"),
]
