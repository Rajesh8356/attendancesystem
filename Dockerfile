FROM python:3.9-slim

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install system dependencies (minimal required)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    libpq-dev \
    libopenblas-dev \
    liblapack-dev \
    && rm -rf /var/lib/apt/lists/*

# Create a modified requirements.txt without dlib
COPY requirements.txt .
RUN grep -v "^dlib==" requirements.txt > requirements-no-dlib.txt

# Install all Python packages except dlib
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements-no-dlib.txt

# Install pre-compiled dlib wheel (Python 3.9, Linux x86_64)
RUN pip install --no-cache-dir https://github.com/z-mahmud22/Dlib_Wheels/raw/main/dlib-19.24.2-cp39-cp39-linux_x86_64.whl

# Install face_recognition (depends on dlib)
RUN pip install --no-cache-dir face-recognition==1.3.0

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p instance static/uploads recordings logs templates/errors

# Set environment variables
ENV FLASK_APP=app.py \
    FLASK_ENV=production \
    PYTHONPATH=/app

# Create non-root user
RUN useradd -m -u 1000 attendance && chown -R attendance:attendance /app
USER attendance

EXPOSE $PORT

CMD gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:$PORT wsgi:app
