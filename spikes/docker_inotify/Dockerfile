FROM python:3.12-slim
WORKDIR /app
RUN pip install watchdog
COPY watch.py .
CMD ["python", "watch.py"]
