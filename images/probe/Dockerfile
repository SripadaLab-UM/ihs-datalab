# A tiny stand-in for the agent image, used only by the Safety check in CI.
# The Safety check tests the sealing around the agent (network, gateway,
# relay, mounts, privileges), not the tools inside it, so CI doesn't need to
# build the full 4.6 GB agent image. Same user, folders, and start-up shape.
FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10004 agent \
    && mkdir -p /work /codex-home /inputs /data/oracle \
    && chown agent:agent /work /codex-home
USER agent
WORKDIR /work
ENV CODEX_HOME=/codex-home
CMD ["sleep", "infinity"]
