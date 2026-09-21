FROM python:3.12-slim
WORKDIR /app
COPY app ./app
RUN mkdir -p /data
ENV PORT=8080 DATA_DIR=/data APP_TZ=CST-8
EXPOSE 8080
CMD ["python", "-u", "-m", "app.main"]
