FROM debian:stable AS prosody

RUN DEBIAN_FRONTEND=noninteractive apt update && \
    apt install extrepo -y && \
    extrepo enable prosody && \
    apt update && \
    apt remove lua5.1 -y && \
    apt install liblua5.2-dev prosody lua5.2 sudo -y && \
    apt clean && \
    rm -rf /var/lib/apt/lists/*


RUN prosodyctl install https://modules.prosody.im/rocks/mod_privilege-19-1.src.rock

RUN mkdir -p /var/run/prosody && chown prosody:prosody /var/run/prosody

ENTRYPOINT /bin/bash -c "sudo -u prosody prosody -F"

FROM prosody AS prosody-dev

RUN prosodyctl register test localhost password

FROM python:3.9-bullseye AS poetry

RUN pip install "poetry==1.1.13" wheel

FROM poetry AS builder

WORKDIR slidge
COPY poetry.lock pyproject.toml /slidge/
RUN poetry export --without-hashes > /slidge/requirements.txt
RUN poetry export --without-hashes --extras telegram > /slidge/requirements-telegram.txt
RUN poetry export --without-hashes --extras signal > /slidge/requirements-signal.txt
RUN poetry export --without-hashes --extras mattermost > /slidge/requirements-mattermost.txt
RUN poetry export --without-hashes --extras facebook > /slidge/requirements-facebook.txt

FROM poetry AS tdlib

RUN apt update && apt install git -y

WORKDIR /
RUN git clone https://github.com/pylakey/aiotdlib.git
WORKDIR /aiotdlib
RUN git checkout tags/0.18.0
RUN poetry install
RUN poetry run aiotdlib_generator
RUN poetry build

FROM python:3.9-bullseye AS slidge

RUN python3 -m venv /venv/
ENV PATH /venv/bin:$PATH

WORKDIR slidge
COPY --from=builder /slidge/requirements.txt /slidge/requirements.txt
RUN pip install -r ./requirements.txt && pip cache purge

COPY ./slidge /slidge

STOPSIGNAL SIGINT

RUN mkdir -p /var/lib/slidge

COPY --from=tdlib /aiotdlib/dist/* /tmp
RUN pip install /tmp/*.whl

RUN DEBIAN_FRONTEND=noninteractive apt update && \
  apt install libc++1 -y && \
  rm -rf /var/lib/apt/lists/*

ENTRYPOINT ["python", "-m", "slidge"]

FROM slidge AS slidge-dev

COPY --from=builder /slidge/*.txt /slidge/

RUN for f in /slidge/*.txt; do pip install -r $f; done && pip cache purge

RUN pip install watchdog[watchmedo] && pip cache purge

ENTRYPOINT ["watchmedo", "auto-restart", "--directory=/slidge/slidge", "--pattern=*.py", "-R", "--", "python", "-m", "slidge"]

#FROM slidge AS discord
#
#RUN apt install git
#
#WORKDIR /
#RUN git clone https://git.polynom.me/PapaTutuWawa/xmpp-discord-bridge.git
#WORKDIR /xmpp-discord-bridge
#
#RUN mkdir /avatars
#
#RUN python setup.py install
#
#ENTRYPOINT ["xmpp-discord-bridge", "--debug"]