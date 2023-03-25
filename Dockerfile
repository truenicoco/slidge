ARG PYTHONVER=3.9
## Base build stage for Slidge, prepares and installs common dependencies.
FROM docker.io/library/python:$PYTHONVER-slim AS builder
ARG PYTHONVER
ENV PATH="/venv/bin:/root/.local/bin:$PATH"

RUN apt-get update -y && apt-get install -y --no-install-recommends \
    ca-certificates curl python3-slixmpp-lib git gcc g++

RUN python3 -m venv /venv && python3 -m pip install wheel
RUN curl -fL https://install.python-poetry.org | python3 -

# copy compiled stringprep module
RUN mkdir -p /venv/lib/python$PYTHONVER/site-packages/slixmpp
RUN cp /usr/lib/python3/dist-packages/slixmpp/* /venv/lib/python$PYTHONVER/site-packages/slixmpp/

WORKDIR /build

# Only copy files used to define dependencies, so this is steps can be in cache
# as long as we don't touch the deps.
COPY poetry.lock pyproject.toml /build/

# default=install all deps.
ARG SLIDGE_PLUGIN="signal facebook telegram skype mattermost steam discord"

# some plugins don't have specific deps, so --extras=PLUGIN fails: fallback to slidge core deps
RUN poetry export --extras="$SLIDGE_PLUGIN" --without-hashes > requirements.txt \
    || poetry export --without-hashes > requirements.txt
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

WORKDIR /var/lib/slidge
COPY --from=builder /venv /venv

STOPSIGNAL SIGINT
USER slidge

ENTRYPOINT ["python", "-m", "slidge"]

## Base execution stage for Slidge; most plugins can run in this stage with `SLIDGE_LEGACY_MODULE` set.
FROM base as slidge
ARG PYTHONVER

# Copy late to be cache-friendly.
COPY slidge /venv/lib/python$PYTHONVER/site-packages/slidge

## Plugin-specific build stages. Certain plugins require additional dependencies and/or preparation,
## and can thus only be executed in these stages.
FROM slidge AS slidge-signal
ENV SLIDGE_LEGACY_MODULE=slidge.plugins.signal

FROM slidge AS slidge-facebook
ENV SLIDGE_LEGACY_MODULE=slidge.plugins.facebook

FROM slidge AS slidge-steam
ENV SLIDGE_LEGACY_MODULE=slidge.plugins.steam

FROM base AS slidge-telegram
ENV SLIDGE_LEGACY_MODULE=slidge.plugins.telegram

USER root
# libc++ required by tdlib
RUN apt-get update -y && apt-get install -y --no-install-recommends \
    libc++1

USER slidge

COPY slidge /venv/lib/python$PYTHONVER/site-packages/slidge

FROM slidge AS slidge-skype
ENV SLIDGE_LEGACY_MODULE=slidge.plugins.skype

FROM slidge AS slidge-hackernews
ENV SLIDGE_LEGACY_MODULE=slidge.plugins.hackernews

FROM slidge AS slidge-mattermost
ENV SLIDGE_LEGACY_MODULE=slidge.plugins.mattermost

FROM slidge AS slidge-discord
ENV SLIDGE_LEGACY_MODULE=slidge.plugins.discord

FROM base AS slidge-whatsapp
ARG PYTHONVER
ENV SLIDGE_LEGACY_MODULE=slidge.plugins.whatsapp
ENV GOBIN="/usr/local/bin"

USER root
RUN echo "deb http://deb.debian.org/debian bullseye-backports main" > /etc/apt/sources.list.d/backports.list && \
    apt update -y && apt install -yt bullseye-backports golang

RUN go install github.com/go-python/gopy@latest && \
    go install golang.org/x/tools/cmd/goimports@latest

RUN python -m pip install pybindgen

COPY slidge/plugins/whatsapp/*.go slidge/plugins/whatsapp/go.* /whatsapp/
RUN cd /whatsapp && \
    gopy build -output=generated -no-make=true .

RUN rm -Rf /root/go
RUN apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false golang

COPY slidge /venv/lib/python$PYTHONVER/site-packages/slidge
RUN mv /whatsapp/generated /venv/lib/python$PYTHONVER/site-packages/slidge/plugins/whatsapp

USER slidge

## Prosody execution environment for local development.
FROM docker.io/library/debian:stable AS prosody-dev

RUN apt-get update -y && apt-get install -y --no-install-recommends extrepo && extrepo enable prosody
RUN apt-get update -y && apt-get install -y --no-install-recommends lua5.2 liblua5.2-dev luarocks prosody

RUN prosodyctl install --server=https://modules.prosody.im/rocks/ mod_privilege
RUN prosodyctl install --server=https://modules.prosody.im/rocks/ mod_conversejs

RUN mkdir -p /var/run/prosody && chown prosody:prosody /var/run/prosody
RUN prosodyctl register test localhost password

USER prosody
ENTRYPOINT ["prosody", "-F"]

## Slidge execution environment for local development across all plugins.
FROM slidge AS slidge-dev
ARG PYTHONVER
USER root

COPY --from=prosody-dev /etc/prosody/certs/localhost.crt /usr/local/share/ca-certificates/
RUN update-ca-certificates

COPY ./dev/assets /venv/lib/python$PYTHONVER/site-packages/dev/assets
COPY ./dev/watcher.py /watcher.py
RUN pip install watchdog[watchmedo]

# FIXME: how do I dynamically set that 3.9?
ENTRYPOINT ["/venv/bin/python", "/watcher.py", "/venv/lib/python3.9/site-packages/slidge/"]

FROM slidge-dev AS slidge-telegram-dev
ARG TARGETPLATFORM="linux/amd64"

RUN apt-get update -y && apt-get install -y --no-install-recommends \
    libc++1 curl

FROM slidge-dev AS slidge-whatsapp-dev
ENV GOBIN="/usr/local/bin"

RUN echo "deb http://deb.debian.org/debian bullseye-backports main" > /etc/apt/sources.list.d/backports.list && \
    apt update -y && apt install -yt bullseye-backports golang

RUN go install github.com/go-python/gopy@latest && \
    go install golang.org/x/tools/cmd/goimports@latest

RUN pip install pybindgen
