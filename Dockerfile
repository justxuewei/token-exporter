FROM python:3.12-slim

# Install Node.js LTS (via NodeSource) for ccusage tools
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Pre-install ccusage and @ccusage/codex globally so the tools are always
# available inside the container without re-downloading on every run.
RUN npm install -g ccusage @ccusage/codex

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Expose the ccusage binaries to the Python subprocess environment.
# NodeSource installs global binaries to /usr/bin so they are already in PATH;
# this ENV ensures any custom npm prefix is covered as well.
ENV PATH="/usr/local/bin:/usr/bin:${PATH}"
# Default values so the rate-limit polling works out-of-the-box in Docker.
# Override at runtime if you need a specific version (e.g. CCUSAGE_BIN=npx ccusage@latest).
ENV CCUSAGE_BIN=ccusage
ENV CCUSAGE_CODEX_BIN="npx @ccusage/codex"

EXPOSE 14531 14532

CMD ["python", "app.py"]