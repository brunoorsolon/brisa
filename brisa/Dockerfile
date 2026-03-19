# ── Stage 1: build ──────────────────────────────────────────────────────────
# Install build tools and compile all Python dependencies.
# This stage is discarded after the final image is assembled.
FROM python:3.12.9-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    make \
    gcc \
    libc-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: final ──────────────────────────────────────────────────────────
# Clean base image — no build tools, just runtime deps and app code.
FROM python:3.12.9-slim

WORKDIR /app

# Runtime-only system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libusb-1.0-0 \
    udev \
    && rm -rf /var/lib/apt/lists/*

# Copy compiled Python packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY app/ ./app/

# Config and DB live on a mounted volume at /data
RUN mkdir -p /data

EXPOSE 9595

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9595"]
