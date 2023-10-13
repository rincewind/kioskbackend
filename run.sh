#!/bin/sh

cd /usr/src/app

PIPENV_VENV_IN_PROJECT=1

pipenv run python manage.py collectstatic --noinput

pipenv run python manage.py createcachetable

# Danger wil robinson.
pipenv run python manage.py migrate --noinput

uwsgi --ini /usr/src/app/uwsgi.ini

