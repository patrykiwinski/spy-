# Python 3.11 – stabilny pod eventlet / gevent
FROM python:3.11-slim

# Aktualizacja pip
RUN pip install --no-cache-dir --upgrade pip

# Katalog roboczy i kopiowanie projektu
WORKDIR /app
COPY . /app

# Zależności
RUN pip install --no-cache-dir -r requirements.txt

# Render daje PORT w zmiennej środowiskowej
ENV PORT=10000

# Uruchomienie: gunicorn + eventlet (WebSocket)
CMD gunicorn -k eventlet -w 1 --bind 0.0.0.0:$PORT app:app
