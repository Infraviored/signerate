FROM python:3.11-slim

# Install system dependencies for CadQuery and fonts
RUN apt-get update && apt-get install -y \
    libgl1 \
    libfontconfig1 \
    libxrender1 \
    libxext6 \
    libglib2.0-0 \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Copy application code
COPY . .

# Ensure sets and output directory exists
RUN mkdir -p sets

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV FLASK_APP=app.py

EXPOSE 15000

# Use gunicorn with a single worker to keep memory usage low as requested
# Timeout is high because geometry generation can take time
CMD ["gunicorn", "--bind", "0.0.0.0:15000", "--workers", "1", "--threads", "4", "--timeout", "300", "app:app"]
