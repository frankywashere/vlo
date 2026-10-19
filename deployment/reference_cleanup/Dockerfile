FROM us-east4-docker.pkg.dev/charswap-franky-20261013/proof/birefnet-cpu@sha256:acb00d618eee6cf2de5806b9d5931477e9f5d4a8f7b7e61fc1f2ea33f7814eb0
RUN pip install --no-cache-dir fastapi==0.143.1 starlette==1.8.0 uvicorn==0.54.0
COPY app.py /app/app.py
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
