ARG PYTHONVER=3.11
## Base build stage for Slidge, prepares and installs common dependencies.
FROM docker.io/library/python:$PYTHONVER AS builder
ARG PYTHONVER
ENV PATH="/venv/bin:/root/.local/bin:$PATH"

# rust/cargo is for building "cryptography" since they don't provide wheels for arm32
RUN apt-get update -y && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    cargo \
    curl \
    git \
    gcc \
    g++ \
    libffi-dev \
    libssl-dev \
    pkg-config \
    python3-dev \
    rustc

RUN python3 -m venv /venv
RUN ln -s /venv/lib/python$PYTHONVER /venv/lib/python
RUN curl -fL https://install.python-poetry.org | python3 - || (cat /poetry-* && false)

WORKDIR /build

FROM builder AS builder-slidge

# Only copy files used to define dependencies, so this is steps can be in cache
# as long as we don't touch the deps.
COPY poetry.lock pyproject.toml /build/

RUN poetry export > requirements.txt
RUN python3 -m pip install --requirement requirements.txt

## Minimal runtime environment for slidge
# We re-use this for plugins that need extra dependencies, but copy the ./slidge
# dir as the last step to be docker cache-friendly
FROM docker.io/library/python:$PYTHONVER-slim AS base
ARG PYTHONVER
ENV PATH="/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# libidn11: required by compiled stringprep module
# libmagic1: to guess mime type from files
# media-types: to determine file name suffix based on file type
RUN apt-get update -y && apt-get install -y --no-install-recommends \
    libidn11 libmagic1 media-types shared-mime-info

RUN addgroup --system --gid 10000 slidge
RUN adduser --system --uid 10000 --ingroup slidge --home /var/lib/slidge slidge

ENV SLIDGE_LEGACY_MODULE=legacy_module

WORKDIR /var/lib/slidge
COPY --from=builder-slidge /venv /venv

STOPSIGNAL SIGINT
USER slidge

ENTRYPOINT ["python", "-m", "slidge"]

# dev container with hot reload on code change
FROM base AS dev
ARG PYTHONVER

USER root

COPY --from=docker.io/nicocool84/slidge-prosody-dev:latest \
  /etc/prosody/certs/localhost.crt \
  /usr/local/share/ca-certificates/
RUN update-ca-certificates

RUN pip install watchdog[watchmedo]

COPY --from=builder-slidge /venv /venv
COPY ./slidge /venv/lib/python/site-packages/slidge

ENTRYPOINT ["watchmedo", "auto-restart", \
  "--pattern", "*.py", \
  "--directory", "/venv/lib/python/site-packages/legacy_module/", \
  "--recursive", \
  "python", "--", "-m", "slidge", \
  "--jid", "slidge.localhost", \
  "--secret", "secret", \
  "--debug"]
