FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/usr/local/bin:${PATH}"

RUN sed -i 's|://ubuntu.com|mirror.yandex.ru|g; s|://ubuntu.com|mirror.yandex.ru|g' /etc/apt/sources.list.d/ubuntu.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git \
    make \
    jq \
    libatomic1 \
    sudo \
    npm \
    bash-completion \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g n && n lts

RUN userdel -r ubuntu 2>/dev/null || true \
    && groupadd -g 1000 vscode \
    && useradd -m -u 1000 -g 1000 -s /bin/bash vscode \
    && echo "vscode ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

USER vscode
WORKDIR /workspaces

RUN npx --yes promptfoo@latest --version
