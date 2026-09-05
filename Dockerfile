# Razorpay Finance Controller - production container image.
#
# Build:  docker build -t razorpay-finance-controller .
# Run:    docker run -p 8000:8000 \
#           -e GROQ_API_KEY=your_key_here \
#           -e API_KEYS=your-chosen-key \
#           -v finance-controller-jobs:/app/api \
#           -v finance-controller-merchants:/app/agent \
#           razorpay-finance-controller
#
# GROQ_API_KEY / API_KEYS / OPENROUTER_API_KEY / MAX_REQUEST_BODY_BYTES /
# STALE_JOB_TIMEOUT_SECONDS / CORS_ALLOWED_ORIGINS / MERCHANT_CONFIG_DB_PATH
# are all read from the environment at runtime (see api/auth.py,
# api/app.py, api/jobs.py, agent/merchant_config.py) - none are baked
# into this image. API_KEYS is optional (see README.md) but the app
# will print a startup warning to stderr if it's left unset - that
# warning is expected and correct behavior, not a build/run error.
# CORS_ALLOWED_ORIGINS defaults to "*" (permissive) - set it to your
# real frontend's origin if serving the frontend from a different
# host/port than this container. See docs/DECISIONS.md for the real,
# severe CORS gap found and fixed via a comprehensive audit - this
# container was never usable cross-origin before that fix.
#
# TWO named volumes above, not one - a real gap found and fixed (see
# docs/DECISIONS.md): merchant config used to be in-memory only, so a
# single volume over /app/api (the job store's directory) was enough.
# Now that agent/merchant_config.py is also genuinely SQLite-persisted
# (in its own directory, a sibling of api/, not the same one), the
# original single-volume example would have silently left merchant
# settings unpersisted across container restarts despite the code
# itself being fixed - the volume mount needed the same fix as the
# code did.
#
# The job database (jobs.db) lives at api/jobs.db by default
# (JOBS_DB_PATH, see api/jobs.py) - INSIDE the container filesystem,
# which means it's lost on every `docker run`/restart unless a volume
# is mounted over that directory, as in the example above. Named
# explicitly here rather than left as a silent surprise on first
# container restart.

FROM python:3.12-slim

# Install dependencies in their own layer, before copying application
# code - so a code-only change doesn't invalidate the (slower) pip
# install layer on every rebuild. Standard Docker layer-caching
# practice, not specific to this project.
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy only what's needed to RUN the app - not tests/, scripts/, docs/,
# or the empty frontend/ directory (see .dockerignore for the full
# exclusion list). Keeps the runtime image lean and reduces the
# container's attack surface; none of the excluded directories are
# imported by anything on the actual request-serving path.
COPY agent/ agent/
COPY api/ api/
COPY data/ data/
COPY eval/ eval/

# Run as a non-root user - a real, low-cost security improvement many
# container security scanners flag by default, not just a formality.
RUN useradd --create-home --shell /bin/bash appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Pairs naturally with the real GET /health endpoint (see api/app.py,
# docs/DECISIONS.md) - uses Python's own urllib rather than installing
# curl, to avoid adding an extra package just for this one check.
# --start-period gives the app time to actually boot (import everything,
# connect to its database) before the first check counts against it.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=4).read()" || exit 1

# --host 0.0.0.0 is required for the app to be reachable from OUTSIDE
# the container - binding to the default 127.0.0.1 would only accept
# connections from within the container itself, a common real Docker
# gotcha worth getting right the first time rather than debugging later.
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
