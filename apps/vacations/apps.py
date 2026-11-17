"""Приложение «Отпуска»: графики, заявки и маршруты согласования."""
from django.apps import AppConfig


class VacationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.vacations"
    label = "vacations"
    verbose_name = "График отпусков"
