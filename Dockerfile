FROM python:3.12-slim

WORKDIR /operator
COPY requirements.txt operator.py ./
RUN pip install --no-cache-dir -r requirements.txt

CMD ["kopf", "run", "/operator/operator.py", "--verbose"]
