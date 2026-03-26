FROM python:3.9-slim

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install system dependencies with CORRECT package names for Debian 11 (Bullseye)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    make \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    libhdf5-dev \
    libpq-dev \
    wget \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p instance static/uploads recordings logs templates/errors

ENV FLASK_APP=app.py \
    FLASK_ENV=production \
    PYTHONPATH=/app

RUN useradd -m -u 1000 attendance && chown -R attendance:attendance /app
USER attendance

EXPOSE $PORT

CMD gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:$PORT wsgi:app
