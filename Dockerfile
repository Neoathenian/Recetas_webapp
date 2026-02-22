# docker run --rm -p 8080:8080 api-builder-webapp:latest
ARG BASE_IMAGE=webapp-cache:latest
FROM ${BASE_IMAGE}

WORKDIR /app

COPY . .

ENV PORT=8087
ENV PYTHONPATH=/app

EXPOSE 8087

ENTRYPOINT ["python", "0_script_dir_in_sys_path.py","--port","8087"]
