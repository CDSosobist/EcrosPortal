"""Цвета рубрик новостей — в фирменной палитре ГК «ЭКРОС»."""
from django.db import migrations

BRAND_COLORS = {
    "#2f5bd7": "#00875c",
    "#0f9d58": "#00a651",
    "#0e7490": "#0f7b8f",
}


def forwards(apps, schema_editor):
    NewsCategory = apps.get_model("portal", "NewsCategory")
    for old, new in BRAND_COLORS.items():
        NewsCategory.objects.filter(color__iexact=old).update(color=new)


def backwards(apps, schema_editor):
    NewsCategory = apps.get_model("portal", "NewsCategory")
    for old, new in BRAND_COLORS.items():
        NewsCategory.objects.filter(color__iexact=new).update(color=old)


class Migration(migrations.Migration):
    dependencies = [("portal", "0001_initial")]
    operations = [migrations.RunPython(forwards, backwards)]
