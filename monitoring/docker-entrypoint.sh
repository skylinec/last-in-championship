#!/bin/sh

# Wait for database
until PGPASSWORD=$POSTGRES_PASSWORD psql -h "db" -U "user" -d "championship" -c '\q'; do
  echo "Waiting for database..."
  sleep 1
done

# Initialize schema
PGPASSWORD=$POSTGRES_PASSWORD psql -h "db" -U "user" -d "championship" -f schema.sql

# Execute CMD
exec "$@"
