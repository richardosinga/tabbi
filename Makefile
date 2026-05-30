.PHONY: install migrate run deploy

install:
	pip install -r requirements.in
	python manage.py migrate

migrate:
	python manage.py migrate

run:
	python manage.py runserver 8000

# Production: collect static files, run with gunicorn
serve:
	python manage.py collectstatic --noinput
	gunicorn tabbi.wsgi:application --bind 0.0.0.0:8000 --workers 2

# Pull latest world66 content (run from cron daily)
sync-content:
	cd $${WORLD66_DIR:-/opt/world66} && git pull --ff-only

deploy:
	git pull
	pip install -r requirements.in
	python manage.py migrate
	python manage.py collectstatic --noinput
	sudo systemctl restart tabbi
