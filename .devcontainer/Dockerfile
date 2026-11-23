FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/usr/local/bin:${PATH}"

RUN sed -i 's|archive.ubuntu.com|mirror.yandex.ru|g; s|security.ubuntu.com|mirror.yandex.ru|g' /etc/apt/sources.list.d/ubuntu.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git \
    vim \
    xz-utils \
    make \
    jq \
    libatomic1 \
    sudo \
    bash-completion \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://nodejs.org/dist/v24.14.0/node-v24.14.0-linux-x64.tar.xz | tar -xJ -C /usr/local --strip-components=1 \
    && npm install -g n

ARG KUSTOMIZE_VERSION=5.8.0
RUN ARCH=$(dpkg --print-architecture) \
    && curl -fsSL "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize/v${KUSTOMIZE_VERSION}/kustomize_v${KUSTOMIZE_VERSION}_linux_${ARCH}.tar.gz" \
    | tar -xz -C /usr/local/bin kustomize \
    && chmod +x /usr/local/bin/kustomize \
    && kustomize completion bash > /etc/bash_completion.d/kustomize

RUN userdel -r ubuntu 2>/dev/null || true \
    && groupadd -g 1000 vscode \
    && useradd -m -u 1000 -g 1000 -s /bin/bash vscode \
    && echo "vscode ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

USER vscode
WORKDIR /workspaces

RUN npx --yes promptfoo@latest --version
