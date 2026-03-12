FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

COPY . /app

# Ensure Linux entrypoint scripts are executable even if cloned on Windows.
RUN sed -i 's/\r$//' /app/docker/*.sh && chmod +x /app/docker/*.sh

RUN mkdir -p /app/.files/blob_storage
