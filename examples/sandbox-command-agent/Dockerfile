FROM python:3.13-alpine
WORKDIR /app
COPY agent.py .
USER nobody
# ratel.yaml sets the command. This CMD lets `docker run -i` work too.
CMD ["python", "agent.py"]
