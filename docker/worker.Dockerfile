FROM python:3.11-slim-bookworm
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN apt-get update && apt-get install -y --no-install-recommends git util-linux ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1001 coder && useradd --uid 1001 --gid 1001 --no-create-home coder
WORKDIR /opt/institution-lab
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock && python -m copilot download-runtime
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps . \
    && mkdir -p /workspace /control \
    && chown coder:coder /workspace && chmod 2770 /workspace \
    && chmod 700 /root /control
ENV HOME=/root PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
WORKDIR /workspace
ENTRYPOINT ["python", "-I", "-m", "institution_lab.runtime.worker"]
