FROM debian:stable AS prosody

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

FROM python:3.9-bullseye AS poetry

RUN --mount=type=cache,id=slidge-poetry,target=/root/.cache/pip \
    pip install "poetry==1.1.13" wheel

FROM poetry AS builder

RUN python3 -m venv /venv/
ENV PATH /venv/bin:$PATH

WORKDIR slidge
COPY poetry.lock pyproject.toml /slidge/
RUN poetry export --without-hashes > /slidge/requirements.txt
RUN poetry export --without-hashes --extras telegram > /slidge/requirements-telegram.txt
RUN poetry export --without-hashes --extras signal > /slidge/requirements-signal.txt
RUN poetry export --without-hashes --extras mattermost > /slidge/requirements-mattermost.txt
RUN poetry export --without-hashes --extras facebook > /slidge/requirements-facebook.txt

RUN --mount=type=cache,id=pip-slidge-builder,target=/root/.cache/pip \
    pip install -r ./requirements.txt

FROM python:3.9-slim AS slidge-base

COPY --from=builder /venv /venv
ENV PATH /venv/bin:$PATH

RUN mkdir -p /var/lib/slidge

STOPSIGNAL SIGINT

FROM poetry AS builder-tdlib

RUN --mount=type=cache,id=slidge-apt-tdlib,target=/var/cache/apt \
    apt update && apt install git -y
WORKDIR /
RUN git clone https://github.com/pylakey/aiotdlib.git
WORKDIR /aiotdlib
RUN git checkout tags/0.18.0
RUN poetry install
RUN poetry run aiotdlib_generator
RUN poetry build

COPY --from=builder /venv /venv
ENV PATH /venv/bin:$PATH

RUN --mount=type=cache,id=pip-slidge-tdlib,target=/root/.cache/pip \
    pip install /aiotdlib/dist/*.whl

COPY --from=builder /slidge/requirements-telegram.txt /r.txt
RUN --mount=type=cache,id=pip-slidge-tdlib,target=/root/.cache/pip \
    pip install -r /r.txt

FROM slidge-base AS slidge-telegram

RUN --mount=type=cache,id=apt-slidge-telegram,target=/var/cache/apt \
    DEBIAN_FRONTEND=noninteractive apt update && \
    apt install libc++1 -y

COPY --from=builder-tdlib /venv /venv

COPY ./slidge /venv/lib/python3.9/site-packages/slidge

ENTRYPOINT ["python", "-m", "slidge", "--legacy-module=slidge.plugins.telegram"]

FROM slidge-base AS slidge-signal

COPY --from=builder /slidge/requirements-signal.txt /r.txt
RUN --mount=type=cache,id=pip-slidge-signal,target=/root/.cache/pip \
    pip install -r /r.txt

COPY ./slidge /venv/lib/python3.9/site-packages/slidge

ENTRYPOINT ["python", "-m", "slidge", "--legacy-module=slidge.plugins.signal"]

FROM slidge-telegram AS slidge-dev

COPY --from=builder /slidge/*.txt /slidge/

RUN --mount=type=cache,id=slidge-slidge-dev,target=/root/.cache/pip \
    for f in /slidge/*.txt; do pip install -r $f; done

RUN --mount=type=cache,id=slidge-slidge-dev,target=/root/.cache/pip \
    pip install watchdog[watchmedo]

COPY --from=prosody /etc/prosody/certs/localhost.crt /usr/local/share/ca-certificates/
RUN update-ca-certificates

COPY ./assets /venv/lib/python3.9/site-packages/assets

ENTRYPOINT ["watchmedo", "auto-restart", \
            "--directory=/venv/lib/python3.9/site-packages/slidge", "--pattern=*.py", "-R", "--", \
            "python", "-m", "slidge"]
