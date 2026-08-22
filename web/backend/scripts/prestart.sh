#! /usr/bin/env bash

set -e
set -x

# Let the DB start
python -m app.scripts.bootstrap wait-for-database

# Run migrations
alembic upgrade head

# Create initial data in DB
python -m app.scripts.bootstrap seed-initial-data
