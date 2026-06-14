FROM python:3.11-slim

LABEL maintainer="SafeGirl"
LABEL description="SafeGirl Hyderabad Safe Route Backend"

# System dependencies for osmnx (needs libspatialindex for rtree)
RUN apt-get update && apt-get install -y --no-install-recommends \
  libspatialindex-dev \
  libgeos-dev \
  libproj-dev \
  gcc \
  g++ \
  curl \
  && rm -rf /var/lib/apt/lists/*

# Working directory
WORKDIR /app

# Copy and install Python dependencies first (Docker cache layer optimization)
COPY backend/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
  pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY backend/ .

# Create data directory for graph cache
RUN mkdir -p /app/data

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

# Start server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
