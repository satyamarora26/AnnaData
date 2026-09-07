# Agent API container, sized for a platform that scales to zero.
#
# Cold start is the thing to optimise here, not throughput. Every request that
# arrives at an idle service waits for this image to start, so the image
# installs the serving dependencies only - PDF parsing, YAML and the AWS SDK
# live in requirements-tools.txt and never reach the container.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies are copied and installed before the source so that editing code
# does not invalidate the layer that takes all the time to build.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run assigns the port at runtime and expects the process to bind it, so
# it is read from the environment rather than fixed. 8080 is its default and is
# only a fallback for running the image locally.
#
# One worker, deliberately: the platform scales by adding instances, so extra
# workers inside a container multiply memory for no gain and slow the start.
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
