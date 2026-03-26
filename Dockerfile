# Use Python 3.9 slim image
FROM python:3.9-slim

# Set environment variables to prevent interactive prompts
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive

# Set working directory
WORKDIR /app

# Install essential system dependencies only
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    make \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libpq-dev \
    wget \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p /app/instance \
    /app/static/uploads \
    /app/recordings \
    /app/logs \
    /app/templates/errors

# Set Flask environment variables
ENV FLASK_APP=app.py \
    FLASK_ENV=production \
    PYTHONPATH=/app

# Create non-root user and set permissions
RUN useradd -m -u 1000 attendance && \
    chown -R attendance:attendance /app

# Switch to non-root user
USER attendance

# Expose port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

# Run the application
CMD ["gunicorn", \
     "--worker-class", "eventlet", \
     "-w", "1", \
     "--bind", "0.0.0.0:5000", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--log-level", "info", \
     "wsgi:app"]
