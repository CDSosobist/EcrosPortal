.PHONY: help install migrate seed run test check sync dry-run smoke collectstatic superuser clean

PY := .venv/bin/python
PIP := .venv/bin/pip

help:            ## Показать список команд
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install:         ## Создать venv и установить зависимости
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

migrate:         ## Применить миграции
	$(PY) manage.py migrate

seed:            ## Наполнить демонстрационными данными (400 сотрудников)
	$(PY) manage.py seed_demo

run:             ## Запустить dev-сервер на 0.0.0.0:8000
	$(PY) manage.py runserver 0.0.0.0:8000

test:            ## Прогнать тесты
	$(PY) manage.py test

check:           ## Проверить проект и боевые настройки
	$(PY) manage.py check
	-$(PY) manage.py check --deploy

sync:            ## Синхронизировать с Active Directory
	$(PY) manage.py ad_sync

dry-run:         ## Синхронизация без записи
	$(PY) manage.py ad_sync --dry-run

smoke:           ## Дымовая проверка всех страниц
	$(PY) manage.py shell -c "exec(open('scripts/smoke.py').read())"

collectstatic:   ## Собрать статику для боевого режима
	$(PY) manage.py collectstatic --noinput

superuser:       ## Создать администратора
	$(PY) manage.py createsuperuser

clean:           ## Удалить локальную БД и кэш (осторожно!)
	rm -f var/db.sqlite3
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
