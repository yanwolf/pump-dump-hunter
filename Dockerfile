FROM python:3.12-slim
WORKDIR /app
COPY *.py ./
ENV PORT=8080 DATA_DIR=/data
EXPOSE 8080
CMD ["python", "-u", "server.py"]
