FROM ghcr.io/mlflow/mlflow:v3.16.1
# the stock image has neither a Postgres driver nor an S3 client
RUN pip install --no-cache-dir psycopg2-binary==2.9.11 boto3==1.40.61
