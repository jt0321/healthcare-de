#!/bin/sh
set -e

# Install curl and jq
apk add --no-cache curl jq

# Wait for polaris to be healthy
echo "Waiting for Polaris to start..."
until curl -s http://polaris:8182/q/health | grep -q '"status": "UP"'; do
  sleep 1
done

echo "Polaris is up! Obtaining token..."
# Get token
RESPONSE=$(curl -s -X POST -H "Polaris-Realm: POLARIS" \
  "http://polaris:8181/api/catalog/v1/oauth/tokens" \
  -d "grant_type=client_credentials" \
  -d "client_id=root" \
  -d "client_secret=s3cr3t" \
  -d "scope=PRINCIPAL_ROLE:ALL")

TOKEN=$(echo "$RESPONSE" | jq -r '.access_token')

if [ -z "$TOKEN" ] || [ "$TOKEN" = "null" ]; then
  echo "Failed to obtain token. Response: $RESPONSE"
  exit 1
fi

echo "Creating catalog 'default'..."
# Create catalog
CREATE_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" -X POST -H "Polaris-Realm: POLARIS" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  "http://polaris:8181/api/management/v1/catalogs" \
  -d '{
    "catalog": {
      "name": "default",
      "type": "INTERNAL",
      "properties": {
        "default-base-location": "s3://healthcare/iceberg/"
      },
      "storageConfigInfo": {
        "storageType": "S3",
        "allowedLocations": [
          "s3://healthcare/iceberg/"
        ],
        "roleArn": "arn:aws:iam::000000000000:role/dummy",
        "endpoint": "http://minio:9000",
        "pathStyleAccess": true
      }
    }
  }')

if [ "$CREATE_RESPONSE" = "201" ] || [ "$CREATE_RESPONSE" = "200" ]; then
  echo "Catalog 'default' created successfully."
elif [ "$CREATE_RESPONSE" = "409" ]; then
  echo "Catalog 'default' already exists."
else
  echo "Failed to create catalog. HTTP status code: $CREATE_RESPONSE"
  exit 1
fi
