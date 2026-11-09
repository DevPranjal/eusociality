FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends tinyproxy ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY docker/proxy.conf /etc/tinyproxy/tinyproxy.conf
COPY docker/allowed-hosts.txt /etc/tinyproxy/allowed-hosts
RUN sed -i 's/\r$//' /etc/tinyproxy/tinyproxy.conf /etc/tinyproxy/allowed-hosts
ENTRYPOINT ["tinyproxy", "-d"]
