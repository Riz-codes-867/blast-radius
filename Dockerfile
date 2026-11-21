# Same base image the organizers labeled with (linux/amd64). Nothing is downloaded at build time:
# the coverage map and the pinned sqlglot source are committed as plain files under app/.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
ENV PYTHONDONTWRITEBYTECODE=1
COPY app /app
RUN chmod -R a+rX /app && python -m compileall -q /app/core.py
CMD ["python", "-I", "/app/selector.py"]
