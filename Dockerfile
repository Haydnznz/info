FROM python:3.12-slim
WORKDIR /app
COPY app.py config.json ./
COPY backend ./backend
COPY static ./static
ENV PYTHONUNBUFFERED=1
EXPOSE 9000
CMD ["python", "app.py", "api", "--host", "0.0.0.0", "--port", "9000"]
