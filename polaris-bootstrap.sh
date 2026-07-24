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

# catalog_admin only gets CATALOG_MANAGE_ACCESS/METADATA by default. dbt's table
# materialization drops a "__dbt_backup" table on every rerun, which needs
# CATALOG_MANAGE_CONTENT (data-purge) rights too, or Trino gets a 403 from Polaris.
#
# Check first rather than just PUT-and-check-status: re-granting an existing grant
# doesn't 409, it 500s (a duplicate-key error from Polaris's own persistence layer),
# so status-code checking alone can't tell "already granted" from "actually broken".
EXISTING_GRANTS=$(curl -s -H "Polaris-Realm: POLARIS" \
  -H "Authorization: Bearer $TOKEN" \
  "http://polaris:8181/api/management/v1/catalogs/default/catalog-roles/catalog_admin/grants")

if echo "$EXISTING_GRANTS" | jq -e '.grants[]? | select(.privilege == "CATALOG_MANAGE_CONTENT")' > /dev/null 2>&1; then
  echo "CATALOG_MANAGE_CONTENT already granted."
else
  echo "Granting CATALOG_MANAGE_CONTENT to catalog_admin..."
  GRANT_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" -X PUT -H "Polaris-Realm: POLARIS" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    "http://polaris:8181/api/management/v1/catalogs/default/catalog-roles/catalog_admin/grants" \
    -d '{"grant": {"type": "catalog", "privilege": "CATALOG_MANAGE_CONTENT"}}')

  if [ "$GRANT_RESPONSE" = "201" ] || [ "$GRANT_RESPONSE" = "200" ]; then
    echo "CATALOG_MANAGE_CONTENT granted."
  else
    echo "Failed to grant CATALOG_MANAGE_CONTENT. HTTP status code: $GRANT_RESPONSE"
    exit 1
  fi
fi
