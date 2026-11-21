# Same base image the organizers labeled with (linux/amd64).
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
ENV PYTHONDONTWRITEBYTECODE=1
# Only the third-party packages sqlglot's tests import, pinned to the organizers' labeling environment.
COPY app/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --disable-pip-version-check -c /tmp/requirements.lock duckdb pandas numpy setuptools python-dateutil tzdata pytz
# The selector, its coverage map, the pinned sqlglot source and its tests are committed as plain files.
COPY app /app
RUN chmod -R a+rX /app && python -m compileall -q /app/core.py /app/syncpool.py
CMD ["python", "-I", "/app/selector.py"]
