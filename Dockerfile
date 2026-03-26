FROM python:3.9

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install cmake and other dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    cmake \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install dlib (will compile but with proper CMake)
RUN pip install --no-cache-dir dlib==19.24.2

# Install face_recognition
RUN pip install --no-cache-dir face-recognition==1.3.0

# Install other dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p instance static/uploads recordings logs templates/errors

ENV FLASK_APP=app.py \
    FLASK_ENV=production \
    PYTHONPATH=/app

RUN useradd -m -u 1000 attendance && chown -R attendance:attendance /app
USER attendance

EXPOSE $PORT

CMD gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:$PORT wsgi:app
