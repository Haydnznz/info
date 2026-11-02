FROM python:3.12-slim
WORKDIR /app
COPY app.py ./
COPY frontend ./frontend
COPY config ./config
COPY input ./input
COPY processor ./processor
COPY tool ./tool
COPY utils ./utils
COPY web ./web
ENV PYTHONUNBUFFERED=1
EXPOSE 9000
CMD ["python", "app.py", "api", "--host", "0.0.0.0", "--port", "9000"]
