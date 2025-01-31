FROM ubuntu:24.10 AS test-container

ENV DEBIAN_FRONTEND=noninteractive
ENV SDL_AUDIODRIVER=dummy

RUN set -xe; \
    apt-get -qy update && \
    apt-get -qy install \
        python3-pip \
        python3-venv \
        curl \
        jq \
        xvfb \
        xinit \
        x11-utils \
        i3 \
        libepoxy0 \
        libgtk-3-0 \
        libpixman-1-0 \
        libpulse0 \
        libsamplerate0 \
        libsdl2-2.0-0 \
#        x11vnc \
#        ffmpeg \
#        qemu-utils \
#        libc6 \
#        libgcc-s1 \
#        libglib2.0-0 \
#        libpcap0.8 \
#        libssl-dev \
#        libstdc++6 \
        zlib1g \
        perceptualdiff \
        unzip \
        ;

RUN /usr/bin/python3 -m venv /venv && \
    . /venv/bin/activate && \
    pip3 install nxdk-pgraph-test-runner>=0.0.4

COPY --chmod=0770 .docker/entrypoint.sh /bin/entrypoint.sh

WORKDIR /work

COPY .docker/xemu.toml /root/.local/share/xemu/xemu/xemu.toml

#RUN apt-get -qy install \
#    xinit \
#    ;

ENTRYPOINT ["/bin/entrypoint.sh"]
