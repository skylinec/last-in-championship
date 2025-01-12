#!/bin/sh

# Wait for database
until PGPASSWORD=postgres psql -h "db" -U "postgres" -d "championship" -c '\q'; do
  echo "Waiting for database..."
  sleep 1
done

# Initialize schema
PGPASSWORD=postgres psql -h "db" -U "postgres" -d "championship" -f schema.sql

# Execute CMD
exec "$@"
