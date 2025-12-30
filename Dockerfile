# DoVi Convert Docker Image
# Converts Dolby Vision Profile 7 to Profile 8.1 with a web interface

FROM python:3.12-slim-bookworm AS base

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Bash shell (required by dovi_convert script)
    bash \
    # FFmpeg and multimedia tools
    ffmpeg \
    mediainfo \
    mkvtoolnix \
    # Required by dovi_convert script
    jq \
    bc \
    # Networking tools
    curl \
    wget \
    ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install dovi_tool from GitHub releases
ARG DOVI_TOOL_VERSION=2.1.2
RUN ARCH=$(dpkg --print-architecture) && \
    case "$ARCH" in \
        amd64) DOVI_ARCH="x86_64-unknown-linux-musl" ;; \
        arm64) DOVI_ARCH="aarch64-unknown-linux-musl" ;; \
        *) echo "Unsupported architecture: $ARCH" && exit 1 ;; \
    esac && \
    wget -q "https://github.com/quietvoid/dovi_tool/releases/download/${DOVI_TOOL_VERSION}/dovi_tool-${DOVI_TOOL_VERSION}-${DOVI_ARCH}.tar.gz" -O /tmp/dovi_tool.tar.gz && \
    tar -xzf /tmp/dovi_tool.tar.gz -C /usr/local/bin && \
    chmod +x /usr/local/bin/dovi_tool && \
    rm /tmp/dovi_tool.tar.gz

# Download the dovi_convert script (Python v7 version)
# Reference: https://github.com/cryptochrome/dovi_convert
ARG DOVI_CONVERT_VERSION=v7.0.0-beta1
RUN wget -q "https://github.com/cryptochrome/dovi_convert/releases/download/${DOVI_CONVERT_VERSION}/dovi_convert.py" -O /usr/local/bin/dovi_convert && \
    chmod +x /usr/local/bin/dovi_convert

# Set up application directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app/ ./app/
COPY templates/ ./templates/
COPY static/ ./static/

# Create directories for config and media
RUN mkdir -p /config /media

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV MEDIA_PATH=/media
ENV CONFIG_PATH=/config

# Expose web interface port
EXPOSE 8080

# Labels for Unraid and container metadata
LABEL maintainer="smidley" \
      org.opencontainers.image.title="DoVi Convert" \
      org.opencontainers.image.description="Web UI for converting Dolby Vision Profile 7 to Profile 8.1" \
      org.opencontainers.image.source="https://github.com/smidley/dovi-convert-docker" \
      net.unraid.docker.webui="http://[IP]:[PORT:8080]/" \
      net.unraid.docker.icon="https://raw.githubusercontent.com/smidley/dovi-convert-docker/main/icon.png"

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/ || exit 1

# Run the application
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
