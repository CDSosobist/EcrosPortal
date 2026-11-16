"""Общие утилиты: транслитерация, разбор DN, телефоны, имена."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone as dt_timezone

TRANSLIT_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def translit(text: str) -> str:
    """Транслитерация кириллицы в латиницу (для логинов и e-mail)."""
    result = []
    for char in text.lower():
        if char in TRANSLIT_MAP:
            result.append(TRANSLIT_MAP[char])
        elif char.isalnum() or char in "-_. ":
            result.append(char)
        else:
            result.append("")
    return "".join(result)


def make_username(first_name: str, last_name: str, index: int = 0) -> str:
    base = f"{translit(first_name)[:1]}.{translit(last_name)}".replace(" ", "")
    base = re.sub(r"[^a-z0-9._-]", "", base).strip(".")
    if index:
        base = f"{base}{index}"
    return base or f"user{index or 1}"


# ---------------------------------------------------------------------------
# Разбор distinguishedName
# ---------------------------------------------------------------------------
DN_PART_RE = re.compile(r"(?:[A-Za-z]+)=(?:[^,\\]|\\.)+")

IGNORED_OU_NAMES = {
    "users", "user", "пользователи", "сотрудники", "accounts", "people",
    "сотрудники компании", "company users", "employees",
}


def split_dn(dn: str) -> list[tuple[str, str]]:
    """Разбирает DN на пары (атрибут, значение), корректно обрабатывая экранирование."""
    parts: list[tuple[str, str]] = []
    if not dn:
        return parts
    buf = ""
    escape = False
    for char in dn:
        if escape:
            buf += char
            escape = False
            continue
        if char == "\\":
            escape = True
            buf += char
            continue
        if char == ",":
            parts.append(_dn_part(buf))
            buf = ""
            continue
        buf += char
    if buf.strip():
        parts.append(_dn_part(buf))
    return [p for p in parts if p[0]]


def _dn_part(part: str) -> tuple[str, str]:
    if "=" not in part:
        return ("", part.strip())
    attr, _, value = part.partition("=")
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    return (attr.strip().upper(), value)


def dn_ou_chain(dn: str) -> list[tuple[str, str]]:
    """Возвращает цепочку OU из DN: от корневого подразделения к вложенному.

    Каждый элемент — (DN подразделения, имя).
    """
    parts = split_dn(dn)
    ous: list[tuple[str, str]] = []
    for index in range(len(parts)):
        attr, value = parts[index]
        if attr != "OU":
            continue
        if value.strip().lower() in IGNORED_OU_NAMES:
            continue
        sub_dn = ",".join(f"{a}={v}" for a, v in parts[index:])
        ous.append((sub_dn, value))
    ous.reverse()
    return ous


def cn_from_dn(dn: str) -> str:
    for attr, value in split_dn(dn):
        if attr == "CN":
            return value
    return dn


def domain_from_base_dn(base_dn: str) -> str:
    labels = [value for attr, value in split_dn(base_dn) if attr == "DC"]
    return ".".join(labels)


def slug_code(name: str, fallback: str = "dept") -> str:
    """Устойчивый код подразделения/роли из произвольного названия."""
    code = translit(name).strip().lower()
    code = re.sub(r"[^a-z0-9]+", "-", code).strip("-")
    return code[:70] or fallback


# ---------------------------------------------------------------------------
# Форматирование
# ---------------------------------------------------------------------------
def format_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return ""
    if len(digits) == 4:
        return digits
    if len(digits) == 11 and digits[0] in "78":
        return f"+7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    return raw


def parse_ad_datetime(value) -> datetime | None:
    """Разбирает даты/время из атрибутов AD (20240115120000.0Z и ISO-варианты)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return _aware(value)
    text = str(value).strip()
    if not text or text.startswith("0"):
        return None
    cleaned = text.split(".")[0].replace("Z", "").replace("T", " ")
    candidates = [
        (cleaned[:14], "%Y%m%d%H%M%S"),
        (cleaned[:19], "%Y-%m-%d %H:%M:%S"),
        (cleaned[:10], "%Y-%m-%d"),
        (cleaned[:8], "%Y%m%d"),
    ]
    for raw, fmt in candidates:
        try:
            return _aware(datetime.strptime(raw, fmt))
        except ValueError:
            continue
    try:
        return _aware(datetime.fromisoformat(str(value)))
    except ValueError:
        return None


def _aware(moment: datetime) -> datetime:
    """AD отдаёт время в UTC — помечаем пояс, чтобы Django не ругался на naive."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=dt_timezone.utc)
    return moment


def parse_ad_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text or text.startswith("0"):
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None
