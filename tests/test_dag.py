"""
Test DAG workflow for validating Airflow pipeline scheduling.
"""
from datetime import datetime, timedelta

from airflow import DAG
from airflow.providers.standard.operators.python import PythonOperator


default_args = {
    "owner": "llm-data-engineering",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}


def extract(**context):
    print("Extracting data...")
    data = {"records": [1, 2, 3], "source": "test"}
    context["ti"].xcom_push(key="raw_data", value=data)
    return data


def transform(**context):
    raw_data = context["ti"].xcom_pull(key="raw_data", task_ids="extract_task")
    print(f"Transforming data: {raw_data}")
    transformed = {
        "records": [r * 2 for r in raw_data["records"]],
        "source": raw_data["source"],
        "transformed": True,
    }
    context["ti"].xcom_push(key="transformed_data", value=transformed)
    return transformed


def load(**context):
    transformed_data = context["ti"].xcom_pull(key="transformed_data", task_ids="transform_task")
    print(f"Loading data: {transformed_data}")
    print("Data loaded successfully.")
    return True


with DAG(
    dag_id="test_etl_pipeline",
    default_args=default_args,
    description="A simple test ETL pipeline DAG",
    schedule=timedelta(days=1),
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["test", "etl"],
) as dag:
    extract_task = PythonOperator(
        task_id="extract_task",
        python_callable=extract,
    )

    transform_task = PythonOperator(
        task_id="transform_task",
        python_callable=transform,
    )

    load_task = PythonOperator(
        task_id="load_task",
        python_callable=load,
    )

    extract_task >> transform_task >> load_task
