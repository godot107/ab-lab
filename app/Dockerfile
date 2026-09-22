FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py assignment.py chat.py snapshot.py snapshot.json store.py ./
COPY templates/ templates/
COPY static/ static/

RUN useradd --system --uid 1001 appuser && mkdir -p /data && chown appuser /data
USER appuser

EXPOSE 5000

# 2 workers keeps memory in budget on a 512 MB Lightsail nano instance.
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:5000", \
     "--access-logfile", "-", "--error-logfile", "-", "app:app"]
