## Base build stage for Slidge, prepares and installs common dependencies.
FROM docker.io/library/python:3.9-slim AS builder
ENV PATH="/venv/bin:/root/.local/bin:$PATH"

RUN apt-get update -y && apt-get install -y --no-install-recommends \
    ca-certificates curl

RUN python3 -m venv /venv && python3 -m pip install wheel
RUN curl -fL https://install.python-poetry.org | python3 -

WORKDIR /build
# only copy files used to define dependencies, so this is steps can be in cache
# as long as we don't touch the deps
COPY poetry.lock /build
COPY pyproject.toml /build

RUN poetry export --extras="signal facebook telegram skype mattermost steam discord" > requirements.txt && \
    python3 -m pip install --requirement requirements.txt

RUN rm -Rf /build

## Minimal runtime environment for slidge
# We re-use this for plugins that need extra dependencies, but copy the ./slidge
# dir as the last step to be docker cache-friendly
FROM docker.io/library/python:3.9-slim AS base
ENV PATH="/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

RUN addgroup --system --gid 10000 slidge
RUN adduser --system --uid 10000 --ingroup slidge --home /var/lib/slidge slidge

WORKDIR /var/lib/slidge
COPY --from=builder /venv /venv

STOPSIGNAL SIGINT
USER slidge

## Base execution stage for Slidge; most plugins can run in this stage with `SLIDGE_LEGACY_MODULE` set.
FROM base as slidge
# copy late to be cache-friendly
COPY slidge /venv/lib/python3.9/site-packages/slidge

ENTRYPOINT ["python", "-m", "slidge"]

## Plugin-specific build stages. Certain plugins require additional dependencies and/or preparation,
## and can thus only be executed in these stages.
FROM slidge AS slidge-signal
ENV SLIDGE_LEGACY_MODULE=slidge.plugins.signal

FROM slidge AS slidge-facebook
ENV SLIDGE_LEGACY_MODULE=slidge.plugins.facebook

FROM base AS slidge-telegram
ENV SLIDGE_LEGACY_MODULE=slidge.plugins.telegram

USER root
RUN apt-get update -y && apt-get install -y --no-install-recommends \
    git g++ cmake make gperf zlib1g-dev libssl-dev

RUN git clone --depth 1 https://github.com/pylakey/td /td && mkdir -p /td/build && cd /td/build && \
    cmake -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX:PATH=/usr -DTD_ENABLE_LTO=ON .. && \
    CMAKE_BUILD_PARALLEL_LEVEL=$(grep -c processor /proc/cpuinfo) cmake --build . --target install && \
    rm -Rf /td

RUN apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
    git g++ cmake make

USER slidge

COPY slidge /venv/lib/python3.9/site-packages/slidge

FROM slidge AS slidge-skype
ENV SLIDGE_LEGACY_MODULE=slidge.plugins.skype

FROM slidge AS slidge-hackernews
ENV SLIDGE_LEGACY_MODULE=slidge.plugins.hackernews

FROM slidge AS slidge-mattermost
ENV SLIDGE_LEGACY_MODULE=slidge.plugins.mattermost

FROM slidge AS slidge-discord
ENV SLIDGE_LEGACY_MODULE=slidge.plugins.discord

FROM base AS slidge-whatsapp
ENV SLIDGE_LEGACY_MODULE=slidge.plugins.whatsapp
ENV GOBIN="/usr/local/bin"

USER root
RUN echo "deb http://deb.debian.org/debian bullseye-backports main" > /etc/apt/sources.list.d/backports.list && \
    apt update -y && apt install -yt bullseye-backports golang

RUN go install github.com/go-python/gopy@latest && \
    go install golang.org/x/tools/cmd/goimports@latest

RUN python -m pip install pybindgen
RUN cd /venv/lib/python3.9/site-packages/slidge/plugins/whatsapp && \
    gopy build -output=generated -no-make=true .

RUN rm -Rf /root/go
RUN apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false golang

USER slidge

COPY slidge /venv/lib/python3.9/site-packages/slidge

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
USER root

COPY --from=prosody-dev /etc/prosody/certs/localhost.crt /usr/local/share/ca-certificates/
RUN update-ca-certificates

COPY ./assets /venv/lib/python3.9/site-packages/assets
COPY watcher.py /watcher.py
RUN pip install watchdog[watchmedo]

ENTRYPOINT ["/venv/bin/python", "/watcher.py", "/venv/lib/python3.9/site-packages/slidge/"]

FROM slidge-dev AS slidge-telegram-dev
ARG TARGETPLATFORM="linux/amd64"

RUN apt-get update -y && apt-get install -y --no-install-recommends \
    libc++1 curl

RUN if [ "$TARGETPLATFORM" = "linux/arm64" ]; then \
      cd /venv/lib/python3.9/site-packages/aiotdlib/tdlib && \
      rm -f *amd64.so && \
      curl -O https://slidge.im/libtdjson_linux_arm64.so; \
    fi

FROM slidge-dev AS slidge-whatsapp-dev
ENV GOBIN="/usr/local/bin"

RUN echo "deb http://deb.debian.org/debian bullseye-backports main" > /etc/apt/sources.list.d/backports.list && \
    apt update -y && apt install -yt bullseye-backports golang

RUN go install github.com/go-python/gopy@latest && \
    go install golang.org/x/tools/cmd/goimports@latest

RUN pip install pybindgen
