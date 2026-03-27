FROM python:3.11-slim

WORKDIR /app

# Install cron
RUN apt-get update && apt-get install -y --no-install-recommends cron && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Copy cron schedule and entrypoint
COPY cron/pipeline-cron /etc/cron.d/pipeline-cron
RUN chmod 0644 /etc/cron.d/pipeline-cron && crontab /etc/cron.d/pipeline-cron

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
