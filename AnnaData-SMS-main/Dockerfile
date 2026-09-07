# SMS bridge container.
#
# The bridge answers a gateway webhook that gives up in about thirty seconds, so
# start-up time is the constraint that matters. Its dependency list is small,
# which is why this image starts quickly and why it should stay small.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The port is assigned at runtime by the platform; 8080 is only a local
# fallback. One worker, because the platform scales by adding instances.
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
