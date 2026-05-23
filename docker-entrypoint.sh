#!/bin/sh
set -e

# Fix ownership of mounted volumes so appuser can write to them
chown -R appuser:appuser /wiki-data
chown -R appuser:appuser /app

# Drop privileges and execute the main command
exec gosu appuser "$@"
