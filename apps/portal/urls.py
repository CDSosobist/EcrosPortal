"""Маршруты портала."""
from django.urls import path

from . import views

app_name = "portal"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("directory/", views.directory, name="directory"),
    path("directory/<int:pk>/", views.employee_detail, name="employee_detail"),
    path("org/", views.org_chart, name="org_chart"),
    path("news/", views.news_list, name="news_list"),
    path("news/new/", views.news_edit, name="news_new"),
    path("news/<slug:slug>/", views.news_detail, name="news_detail"),
    path("news/<int:pk>/edit/", views.news_edit, name="news_edit"),
    path("knowledge/", views.kb_list, name="kb_list"),
    path("knowledge/new/", views.kb_edit, name="kb_new"),
    path("knowledge/<slug:slug>/", views.kb_detail, name="kb_detail"),
    path("knowledge/<int:pk>/edit/", views.kb_edit, name="kb_edit"),
    path("tickets/", views.tickets, name="tickets"),
    path("tickets/new/", views.ticket_create, name="ticket_new"),
    path("tickets/<int:pk>/", views.ticket_detail, name="ticket_detail"),
    path("tickets/<int:pk>/comment/", views.ticket_comment, name="ticket_comment"),
    path("tickets/<int:pk>/update/", views.ticket_update, name="ticket_update"),
    path("search/", views.search, name="search"),
    path("notifications/", views.notifications, name="notifications"),
    path("notifications/read/", views.notifications_read, name="notifications_read"),
]
