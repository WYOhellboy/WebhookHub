#!/bin/bash
set -e

# Start the ingest-only app on 8181 in the background
uvicorn app.ingest:app --host 0.0.0.0 --port 8181 &

# Start the full dashboard app on 8080 in the foreground (PID 1)
exec uvicorn app.main:app --host 0.0.0.0 --port 8080
