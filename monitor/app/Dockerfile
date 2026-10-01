FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py entrypoint.sh ./

# /data is a named volume: the downloaded Hiring Lab data and the data-quality history
# survive container rebuilds. Created here so the volume inherits appuser ownership.
RUN useradd --system --create-home --uid 1001 appuser \
 && mkdir -p /data && chown appuser /data \
 && chmod +x entrypoint.sh
USER appuser

EXPOSE 8050
ENTRYPOINT ["./entrypoint.sh"]
