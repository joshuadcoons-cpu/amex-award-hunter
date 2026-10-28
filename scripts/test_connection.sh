#!/bin/bash
cd "$(dirname "$0")/.."
exec .venv/bin/python scripts/test_connection.py "$@"
