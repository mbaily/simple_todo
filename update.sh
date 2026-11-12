#!/bin/bash
set -euo pipefail
git pull
sudo ./deploy.py "$@"

