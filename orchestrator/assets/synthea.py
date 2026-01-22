import os
import time
from dagster import asset, Output, MetadataValue

@asset(
    description="Waits for Synthea data to be generated in the shared volume."
)
def raw_patients_csv() -> str:
    # Path inside the orchestrator container to wait for
    csv_path = "/opt/dagster/app/data/csv/patients.csv"
    
    # Simple polling (could be improved with a Sensor in real life, but Asset is fine here)
    timeout = 600
    start = time.time()
    
    while not os.path.exists(csv_path):
        if time.time() - start > timeout:
            raise TimeoutError("Synthea data not found after timeout")
        time.sleep(5)
        
    return csv_path
