"""Перевод цветов видов отпуска на фирменную палитру ГК «ЭКРОС».

Старые демонстрационные цвета (синий/бирюзовый/фиолетовый) заменяются на
корпоративные: зелёный #00875c, тёмно-зелёный #0c6d4f, нейтральный серый и
янтарный акцент. Пользовательские цвета, заданные вручную, не затрагиваются —
меняются только известные значения по умолчанию.
"""
from django.db import migrations

BRAND_COLORS = {
    "#2f5bd7": "#00875c",
    "#0e7490": "#0c6d4f",
    "#94a3b8": "#8e979e",
    "#7c3aed": "#b16c00",
    "#3b82f6": "#00875c",
}


def forwards(apps, schema_editor):
    VacationType = apps.get_model("vacations", "VacationType")
    for old, new in BRAND_COLORS.items():
        VacationType.objects.filter(color__iexact=old).update(color=new)


def backwards(apps, schema_editor):
    VacationType = apps.get_model("vacations", "VacationType")
    for old, new in BRAND_COLORS.items():
        VacationType.objects.filter(color__iexact=new).update(color=old)


class Migration(migrations.Migration):
    dependencies = [("vacations", "0001_initial")]
    operations = [migrations.RunPython(forwards, backwards)]
