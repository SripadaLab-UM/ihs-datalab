FROM node:22-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl python3 ca-certificates procps dnsutils \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g @openai/codex@0.157.1 && npm cache clean --force
# node image already has uid 1000 "node"; create a dedicated agent user
RUN useradd -m -u 1001 -s /bin/bash agent && mkdir -p /work && chown agent:agent /work
USER agent
WORKDIR /work
ENV CODEX_HOME=/codex_home
CMD ["sleep", "infinity"]
