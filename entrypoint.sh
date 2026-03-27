#!/bin/bash
# Export all env vars to a file so cron jobs can access them
printenv | grep -v "no_proxy" >> /etc/environment

# Start cron in the foreground
echo "[AMLWire] Container started. Cron scheduled at 20:00 UTC daily."
echo "[AMLWire] To run manually: docker exec amlwire python main.py"
cron -f
