"""Маршруты раздела «График отпусков»."""
from django.urls import path

from . import views

app_name = "vacations"

urlpatterns = [
    path("", views.index, name="index"),
    path("new/", views.request_create, name="request_new"),
    path("requests/", views.request_list, name="request_list"),
    path("requests/<int:pk>/", views.request_detail, name="request_detail"),
    path("requests/<int:pk>/edit/", views.request_edit, name="request_edit"),
    path("requests/<int:pk>/submit/", views.request_submit, name="request_submit"),
    path("requests/<int:pk>/cancel/", views.request_cancel, name="request_cancel"),
    path("steps/<int:pk>/approve/", views.step_approve, name="step_approve"),
    path("steps/<int:pk>/reject/", views.step_reject, name="step_reject"),
    path("approvals/", views.approvals, name="approvals"),
    path("team/", views.team_view, name="team"),
    path("schedule/", views.schedule, name="schedule"),
    path("schedule/<int:year>/", views.schedule, name="schedule_year"),
    path("schedule/<int:year>/export/", views.schedule_export, name="schedule_export"),
    path("calendar/", views.calendar_view, name="calendar"),
    path("manage/", views.manage_index, name="manage"),
    path("manage/periods/", views.manage_periods, name="manage_periods"),
    path("manage/balances/", views.manage_balances, name="manage_balances"),
    path("manage/chains/", views.manage_chains, name="manage_chains"),
    path("manage/chains/new/", views.chain_edit, name="chain_new"),
    path("manage/chains/<int:pk>/", views.chain_edit, name="chain_edit"),
    path("manage/chains/<int:pk>/delete/", views.chain_delete, name="chain_delete"),
    path("manage/types/", views.manage_types, name="manage_types"),
    path("manage/holidays/", views.manage_holidays, name="manage_holidays"),
]
