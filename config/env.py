"""Минималистичный загрузчик .env без внешних зависимостей.

Портал должен разворачиваться в закрытом контуре, где установка
дополнительных python-пакетов не всегда возможна, поэтому чтение
переменных окружения реализовано собственными силами.
"""
from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: Path | str) -> dict[str, str]:
    """Читает файл .env и складывает значения в os.environ.

    Уже установленные переменные окружения имеют приоритет — это позволяет
    переопределять настройки из systemd/docker без правки файлов.
    """
    path = Path(path)
    loaded: dict[str, str] = {}
    if not path.is_file():
        return loaded

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if not key:
            continue
        loaded[key] = value
        os.environ.setdefault(key, value)
    return loaded


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    raw = env(name, "")
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on", "да"}


def env_int(name: str, default: int) -> int:
    raw = env(name, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_list(name: str, default: Iterable[str] = ()) -> list[str]:
    raw = env(name, "")
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]
