FROM python:3.13-slim-bookworm@sha256:fa16c23a012723c24eef39cc755a27babbba16b711e5d1e6cf16225ad21637cd
COPY dependencies/ /opt/dependencies/
COPY package/ /opt/package/
COPY e2e.py /opt/e2e.py
ENV PATH="/opt/dependencies:${PATH}" PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
WORKDIR /tmp
ENTRYPOINT ["python3", "/opt/e2e.py"]
