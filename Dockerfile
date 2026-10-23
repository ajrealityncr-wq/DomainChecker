# Runs the web app in a container. Publish the port on this computer only, for example:
#   docker build -t domain-search .
#   docker run --rm -p 127.0.0.1:8000:8000 --env-file .env -v "$(pwd)/output:/app/output" domain-search
# Then open http://127.0.0.1:8000. The app has no login, so never publish the port on 0.0.0.0.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./
COPY static ./static

# Inside the container the app must listen on all interfaces so the published port reaches it.
EXPOSE 8000
CMD ["uvicorn", "web_app:app", "--host", "0.0.0.0", "--port", "8000"]
