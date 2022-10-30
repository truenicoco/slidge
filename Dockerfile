FROM docker.io/library/debian:stable AS prosody

RUN --mount=type=cache,id=slidge-apt-prosody,target=/var/cache/apt \
    DEBIAN_FRONTEND=noninteractive apt update && \
    apt install extrepo -y && \
    extrepo enable prosody && \
    apt update && \
    apt remove lua5.1 -y && \
    apt install liblua5.2-dev prosody lua5.2 sudo -y && \
    apt clean && \
    rm -rf /var/lib/apt/lists/*

RUN prosodyctl install --server=https://modules.prosody.im/rocks/ mod_privilege
RUN prosodyctl install --server=https://modules.prosody.im/rocks/ mod_conversejs

RUN mkdir -p /var/run/prosody && chown prosody:prosody /var/run/prosody

USER prosody

ENTRYPOINT ["prosody", "-F"]

FROM prosody AS prosody-dev

RUN prosodyctl register test localhost password

FROM docker.io/library/debian:stable AS builder-tdlib

# everything telegram/tdlib-specific would be improved, ie removed, if we fixed
# https://github.com/pylakey/aiotdlib/issues/50

ENV DEBIAN_FRONTEND=noninteractive

RUN apt update
RUN apt install -y git g++ cmake zlib1g-dev gperf libssl-dev
RUN git clone https://github.com/pylakey/td --depth 1
RUN mkdir td/build
WORKDIR td/build
RUN cmake -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX:PATH=/tmp/tdlib/ -DTD_ENABLE_LTO=ON ..
RUN CMAKE_BUILD_PARALLEL_LEVEL=$(grep -c processor /proc/cpuinfo) cmake --build . --target install
RUN ls -la /tmp/tdlib/lib

FROM scratch AS tdlib
COPY --from=builder-tdlib /tmp/tdlib/lib /

FROM docker.io/library/python:3.9-slim AS builder-whatsapp
ENV PATH /root/go/bin:$PATH

RUN echo "deb http://deb.debian.org/debian bullseye-backports main" > /etc/apt/sources.list.d/backports.list && \
    apt update -y && apt install -yt bullseye-backports golang

RUN go install github.com/go-python/gopy@latest && \
    go install golang.org/x/tools/cmd/goimports@latest

WORKDIR /build
COPY slidge/plugins/whatsapp /build

RUN python -m pip install pybindgen
RUN cd /build && gopy build -output=generated -no-make=true .

FROM docker.io/library/python:3.9-slim AS builder

ARG TARGETPLATFORM
ENV DEBIAN_FRONTEND=noninteractive
ENV PATH /venv/bin:/root/.local/bin:$PATH

RUN apt update && apt install gcc python3-slixmpp-lib wget curl -y

RUN python3 -m venv /venv

RUN mkdir -p /venv/lib/python3.9/site-packages/slixmpp
RUN cp /usr/lib/python3/dist-packages/slixmpp/* /venv/lib/python3.9/site-packages/slixmpp/

RUN curl -sSL https://install.python-poetry.org | python3 -

WORKDIR slidge

RUN pip install wheel
COPY poetry.lock pyproject.toml ./
RUN poetry export > r-base.txt
RUN --mount=type=cache,id=slidge-pip-cache,target=/root/.cache/pip \
    pip install -r r-base.txt
ARG PLUGIN="facebook signal telegram skype mattermost steam discord whatsapp"
RUN poetry export --extras "$PLUGIN" > r.txt || true
RUN --mount=type=cache,id=slidge-pip-cache,target=/root/.cache/pip \
    pip install -r r.txt || true

RUN if [ "$PLUGIN" = "telegram" ]; then \
      cd /venv/lib/python3.9/site-packages/aiotdlib/tdlib/ && \
      rm *.dylib && \
      if [ "$TARGETPLATFORM" = "linux/arm64" ]; then \
        rm *amd64.so && \
        wget https://slidge.im/libtdjson_linux_arm64.so; \
      fi; \
    fi

# getting weird errors when uninstalling wheel for some reason!?
# OSError: [Errno 18] Invalid cross-device link: '/venv/lib/python3.9/site-packages/wheel-0.37.1.dist-info/' -> '/venv/lib/python3.9/site-packages/~heel-0.37.1.dist-info'
# OSError: [Errno 39] Directory not empty: '/venv/lib/python3.9/site-packages/wheel-0.37.1.dist-info/'
#RUN pip uninstall wheel -y

FROM docker.io/library/python:3.9-slim AS slidge

ENV PATH /venv/bin:$PATH
ENV PYTHONUNBUFFERED=1
STOPSIGNAL SIGINT

RUN mkdir -p /var/lib/slidge
ENV DEBIAN_FRONTEND=noninteractive

RUN --mount=type=cache,id=slidge-apt-base,target=/var/cache/apt \
    apt update && \
    apt install libidn11 -y && \
    rm -rf /var/lib/apt/lists/*

ARG PLUGIN
RUN --mount=type=cache,id=slidge-apt-base,target=/var/cache/apt \
    if [ "$PLUGIN" = "telegram" ]; then \
      apt update && \
      apt install libc++1 -y && \
      rm -rf /var/lib/apt/lists/*; \
    fi

ENV SLIDGE_LEGACY_MODULE=slidge.plugins.$PLUGIN

COPY --from=builder /venv /venv
COPY ./slidge /venv/lib/python3.9/site-packages/slidge
COPY --from=builder-whatsapp /build/generated /venv/lib/python3.9/site-packages/slidge/plugins/whatsapp/generated

ENTRYPOINT ["python", "-m", "slidge"]

FROM slidge AS slidge-dev

ARG TARGETPLATFORM
ENV PATH /root/go/bin:$PATH

RUN apt update -y && apt install -y libc++1 wget

RUN echo "deb http://deb.debian.org/debian bullseye-backports main" > /etc/apt/sources.list.d/backports.list && \
    apt update -y && apt install -yt bullseye-backports golang

RUN apt update && apt install libc++1 wget -y
RUN go install github.com/go-python/gopy@latest && \
    go install golang.org/x/tools/cmd/goimports@latest

RUN if [ "$TARGETPLATFORM" = "linux/arm64" ]; then \
      cd /venv/lib/python3.9/site-packages/aiotdlib/tdlib/ && \
      rm *amd64.so && \
      wget https://slidge.im/libtdjson_linux_arm64.so; \
    fi

RUN --mount=type=cache,id=slidge-slidge-dev,target=/root/.cache/pip \
    pip install watchdog[watchmedo] pybindgen

COPY --from=prosody /etc/prosody/certs/localhost.crt /usr/local/share/ca-certificates/
RUN update-ca-certificates

COPY ./assets /venv/lib/python3.9/site-packages/assets
COPY watcher.py /watcher.py

ENTRYPOINT ["/venv/bin/python", "/watcher.py", "/venv/lib/python3.9/site-packages/slidge/"]
