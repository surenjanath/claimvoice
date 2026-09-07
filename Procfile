release: python manage.py migrate --noinput
web: gunicorn claimvoice.wsgi --bind 0.0.0.0:$PORT --workers 2 --timeout 60
