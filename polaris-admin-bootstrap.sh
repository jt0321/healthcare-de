#!/bin/sh
set -u

OUTPUT=$(/opt/jboss/container/java/run/run-java.sh bootstrap -r POLARIS -c POLARIS,root,s3cr3t 2>&1)
CODE=$?
echo "$OUTPUT"

if [ "$CODE" -eq 0 ]; then
  exit 0
fi

# The admin tool has no idempotent "bootstrap if needed" mode -- rerunning it against an
# already-bootstrapped Postgres-backed realm errors out. Since the schema/realm already
# exist in that case, treat it as success instead of blocking every subsequent `up`.
if echo "$OUTPUT" | grep -q "already been bootstrapped"; then
  echo "Realm 'POLARIS' already bootstrapped; continuing."
  exit 0
fi

exit "$CODE"
