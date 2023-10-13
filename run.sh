#!/bin/sh

cd /usr/src/app

PIPENV_VENV_IN_PROJECT=1

pipenv run python manage.py collectstatic --noinput

# Danger wil robinson.
pipenv run python manage.py migrate --noinput

uwsgi --ini /usr/src/app/uwsgi.ini

