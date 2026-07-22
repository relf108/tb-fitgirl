# FHS builder for portable AppImages (Ubuntu 22.04 = glibc baseline).
# Do not build release AppImages on NixOS hosts — Flutter embeds /nix/store paths.
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    FLUTTER_HOME=/opt/flutter \
    PATH=/opt/flutter/bin:/opt/flutter/bin/cache/dart-sdk/bin:$PATH \
    PUB_CACHE=/home/builder/.pub-cache \
    # rootless Docker: tar in flutter precache cannot chown archive uids
    TAR_OPTIONS=--no-same-owner

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    unzip \
    xz-utils \
    zip \
    python3 \
    python3-pip \
    python3-venv \
    libpython3.10 \
    libpython3.10-dev \
    build-essential \
    clang \
    cmake \
    ninja-build \
    pkg-config \
    libgtk-3-dev \
    liblzma-dev \
    libstdc++-12-dev \
    file \
    sudo \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 -s /bin/bash builder \
    && echo 'builder ALL=(ALL) NOPASSWD:ALL' >/etc/sudoers.d/builder \
    && mkdir -p /opt/flutter /src \
    && chown -R builder:builder /opt /src

USER builder
WORKDIR /home/builder

# Stable Flutter SDK (linux desktop only).
ARG FLUTTER_VERSION=stable
RUN git clone --depth 1 --branch "${FLUTTER_VERSION}" \
      https://github.com/flutter/flutter.git "${FLUTTER_HOME}" \
    && git config --global --add safe.directory "${FLUTTER_HOME}" \
    && flutter config --no-analytics --enable-linux-desktop \
    && flutter precache --linux \
    && flutter doctor -v

WORKDIR /src
CMD ["bash", "scripts/build-appimage.sh"]
