web: python -c "from app import init_db; init_db()" && gunicorn app:app --worker-class=gthread --threads=4 --timeout=120
