"""Шаблонные фильтры и теги портала."""
from __future__ import annotations

import html
import re

from django import template
from django.utils.html import mark_safe
from django.utils.safestring import SafeString

register = template.Library()

# ---------------------------------------------------------------------------
# Лёгкий markdown для новостей и базы знаний (без внешних зависимостей)
# ---------------------------------------------------------------------------
MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
MD_ITALIC = re.compile(r"(?<![\*\w])\*([^*\n]+)\*(?!\*)")
MD_CODE = re.compile(r"`([^`\n]+)`")


def _inline(text: str) -> str:
    text = MD_LINK.sub(lambda m: f'<a href="{m.group(2)}" target="_blank" rel="noopener">{m.group(1)}</a>', text)
    text = MD_BOLD.sub(r"<strong>\1</strong>", text)
    text = MD_ITALIC.sub(r"<em>\1</em>", text)
    text = MD_CODE.sub(r"<code>\1</code>", text)
    return text


@register.filter(name="markdown")
def markdown_filter(value: str) -> SafeString:
    """Безопасный мини-markdown: заголовки, списки, абзацы, выделения, ссылки."""
    if not value:
        return mark_safe("")
    escaped = html.escape(str(value))
    lines = escaped.splitlines()
    blocks: list[str] = []
    list_items: list[str] = []
    list_kind: str | None = None

    def flush_list():
        nonlocal list_items, list_kind
        if list_items and list_kind:
            blocks.append(f"<{list_kind}>" + "".join(list_items) + f"</{list_kind}>")
        list_items = []
        list_kind = None

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            flush_list()
            continue
        if stripped.startswith("### "):
            flush_list()
            blocks.append(f"<h4>{_inline(stripped[4:])}</h4>")
        elif stripped.startswith("## "):
            flush_list()
            blocks.append(f"<h3>{_inline(stripped[3:])}</h3>")
        elif stripped.startswith("# "):
            flush_list()
            blocks.append(f"<h2>{_inline(stripped[2:])}</h2>")
        elif stripped.startswith(("- ", "* ", "• ")):
            if list_kind not in (None, "ul"):
                flush_list()
            list_kind = "ul"
            list_items.append(f"<li>{_inline(stripped[2:])}</li>")
        elif re.match(r"^\d+\.\s", stripped):
            if list_kind not in (None, "ol"):
                flush_list()
            list_kind = "ol"
            list_items.append(f"<li>{_inline(stripped.split('.', 1)[1].strip())}</li>")
        elif stripped.startswith("&gt; "):
            flush_list()
            blocks.append(f"<blockquote>{_inline(stripped[5:])}</blockquote>")
        elif stripped == "---":
            flush_list()
            blocks.append("<hr>")
        else:
            flush_list()
            blocks.append(f"<p>{_inline(stripped)}</p>")
    flush_list()
    return mark_safe("".join(blocks))


@register.filter(name="avatar_class")
def avatar_class(value) -> str:
    """CSS-класс цвета аватара по объекту сотрудника или логину."""
    color = getattr(value, "avatar_color", None)
    if color is None:
        seed = str(value or "")
        color = (sum(ord(ch) for ch in seed) % 8) + 1
    return f"avatar avatar--c{color}"


@register.filter(name="initials")
def initials(value) -> str:
    explicit = getattr(value, "initials", None)
    if explicit:
        return explicit
    words = str(value or "?").split()
    return "".join(w[0] for w in words[:2]).upper()


@register.filter(name="days_word")
def days_word(value) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return str(value)
    tail = number % 100
    if 11 <= tail <= 14:
        return f"{number} дней"
    last = number % 10
    if last == 1:
        return f"{number} день"
    if 2 <= last <= 4:
        return f"{number} дня"
    return f"{number} дней"


@register.filter(name="ru_date")
def ru_date(value, with_year: bool = True) -> str:
    if not value:
        return "—"
    return value.strftime("%d.%m.%Y") if with_year else value.strftime("%d.%m")


@register.filter(name="ru_datetime")
def ru_datetime(value) -> str:
    if not value:
        return "—"
    from django.utils import timezone

    local = timezone.localtime(value) if timezone.is_aware(value) else value
    return local.strftime("%d.%m.%Y %H:%M")


@register.simple_tag(takes_context=True)
def query_with(context, **kwargs) -> str:
    """Собирает query-string, сохраняя текущие параметры (для фильтров и пагинации)."""
    request = context.get("request")
    params = {}
    if request is not None:
        params = {k: v for k, v in request.GET.items()}
    params.update({k: v for k, v in kwargs.items() if v not in (None, "")})
    for key, value in list(params.items()):
        if value in (None, ""):
            params.pop(key)
    if not params:
        return ""
    from urllib.parse import urlencode

    return "?" + urlencode(params)


@register.filter(name="status_badge")
def status_badge(value) -> str:
    mapping = {
        "draft": "muted",
        "pending": "warn",
        "new": "info",
        "in_progress": "info",
        "waiting": "warn",
        "approved": "success",
        "resolved": "success",
        "closed": "muted",
        "rejected": "danger",
        "cancelled": "muted",
        "skipped": "muted",
        "collecting": "info",
        "review": "warn",
        "low": "muted",
        "normal": "info",
        "high": "warn",
        "critical": "danger",
        "active": "success",
        "vacation": "info",
        "leave": "warn",
        "dismissed": "muted",
    }
    return mapping.get(str(value), "muted")


@register.filter(name="progress_style")
def progress_style(value, total) -> str:
    try:
        total = int(total)
        value = int(value)
    except (TypeError, ValueError):
        return "width:0%"
    if total <= 0:
        return "width:0%"
    return f"width:{min(100, int(value * 100 / total))}%"
