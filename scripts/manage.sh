#!/usr/bin/env bash
# Запуск manage.py с правильным окружением.
#
#   ./scripts/manage.sh migrate
#   ./scripts/manage.sh seed_demo
#   ./scripts/manage.sh runserver 0.0.0.0:8000
#
# Если в проекте есть .venv — используется он. Если pip в системе недоступен
# (закрытый контур, урезанный образ), скрипт подхватывает вендорные зависимости
# из .bootstrap/vendor, положенные туда командой:
#
#   python3 .bootstrap/get-pip.py --target .bootstrap/vendor
#   PYTHONPATH=.bootstrap/vendor python3 -m pip install --target .bootstrap/vendor Django ldap3
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -x ".venv/bin/python" ]; then
    exec .venv/bin/python manage.py "$@"
fi

if [ -d ".bootstrap/vendor" ]; then
    export PYTHONPATH=".bootstrap/vendor${PYTHONPATH:+:$PYTHONPATH}"
fi

exec python3 manage.py "$@"
