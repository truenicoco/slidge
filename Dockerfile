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

FROM docker.io/library/python:3.9-slim AS poetry

RUN --mount=type=cache,id=slidge-poetry,target=/root/.cache/pip \
    pip install "poetry==1.1.13" wheel

FROM poetry AS builder

RUN --mount=type=cache,id=slidge-apt-builder,target=/var/cache/apt \
    DEBIAN_FRONTEND=noninteractive apt update && \
    apt install libidn11-dev python3-dev gcc -y && \
    rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /venv/
ENV PATH /venv/bin:$PATH

RUN --mount=type=cache,id=pip-slidge-builder,target=/root/.cache/pip \
    pip install cython

WORKDIR slidge
COPY poetry.lock pyproject.toml /slidge/
RUN poetry export --without-hashes > /slidge/requirements.txt
RUN poetry export --without-hashes --extras telegram > /slidge/requirements-telegram.txt
RUN poetry export --without-hashes --extras signal > /slidge/requirements-signal.txt
RUN poetry export --without-hashes --extras mattermost > /slidge/requirements-mattermost.txt
RUN poetry export --without-hashes --extras facebook > /slidge/requirements-facebook.txt
RUN poetry export --without-hashes --extras skype > /slidge/requirements-skype.txt
RUN poetry export --without-hashes --extras steam > /slidge/requirements-steam.txt

RUN --mount=type=cache,id=pip-slidge-builder,target=/root/.cache/pip \
    pip install -r ./requirements.txt

RUN pip uninstall cython -y
RUN test -f /venv/lib/python3.9/site-packages/slixmpp/stringprep.cpython-39-*-linux-gnu.so

FROM docker.io/library/python:3.9-slim AS slidge-base

RUN --mount=type=cache,id=slidge-apt-base,target=/var/cache/apt \
    DEBIAN_FRONTEND=noninteractive apt update && \
    apt install libidn11 -y && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /venv /venv
ENV PATH /venv/bin:$PATH
ENV PYTHONUNBUFFERED=1

RUN mkdir -p /var/lib/slidge

STOPSIGNAL SIGINT

FROM slidge-base AS slidge-telegram

RUN --mount=type=cache,id=apt-slidge-telegram,target=/var/cache/apt \
    DEBIAN_FRONTEND=noninteractive apt update && \
    apt install libc++1 -y

COPY --from=builder /slidge/requirements-telegram.txt /r.txt
RUN --mount=type=cache,id=pip-slidge-telegram,target=/root/.cache/pip \
    pip install -r /r.txt

COPY ./slidge /venv/lib/python3.9/site-packages/slidge

ENTRYPOINT ["python", "-m", "slidge", "--legacy-module=slidge.plugins.telegram"]

FROM slidge-base AS slidge-signal

COPY --from=builder /slidge/requirements-signal.txt /r.txt
RUN --mount=type=cache,id=pip-slidge-signal,target=/root/.cache/pip \
    pip install -r /r.txt

COPY ./slidge /venv/lib/python3.9/site-packages/slidge

ENTRYPOINT ["python", "-m", "slidge", "--legacy-module=slidge.plugins.signal"]

FROM slidge-base AS slidge-facebook

COPY --from=builder /slidge/requirements-facebook.txt /r.txt
RUN --mount=type=cache,id=pip-slidge-facebook,target=/root/.cache/pip \
    pip install -r /r.txt

COPY ./slidge /venv/lib/python3.9/site-packages/slidge

ENTRYPOINT ["python", "-m", "slidge", "--legacy-module=slidge.plugins.facebook"]

FROM slidge-base AS slidge-skype

COPY --from=builder /slidge/requirements-skype.txt /r.txt
RUN --mount=type=cache,id=pip-slidge-skype,target=/root/.cache/pip \
    pip install -r /r.txt

COPY ./slidge /venv/lib/python3.9/site-packages/slidge

ENTRYPOINT ["python", "-m", "slidge", "--legacy-module=slidge.plugins.skype"]

FROM slidge-base AS slidge-hackernews

COPY ./slidge /venv/lib/python3.9/site-packages/slidge

ENTRYPOINT ["python", "-m", "slidge", "--legacy-module=slidge.plugins.hackernews"]

FROM slidge-base AS slidge-mattermost

COPY --from=builder /slidge/requirements-mattermost.txt /r.txt
RUN --mount=type=cache,id=pip-slidge-skype,target=/root/.cache/pip \
    pip install -r /r.txt

COPY ./slidge /venv/lib/python3.9/site-packages/slidge

ENTRYPOINT ["python", "-m", "slidge", "--legacy-module=slidge.plugins.mattermost"]

FROM slidge-base AS slidge-steam

COPY ./slidge /venv/lib/python3.9/site-packages/slidge

ENTRYPOINT ["python", "-m", "slidge", "--legacy-module=slidge.plugins.steam"]

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
