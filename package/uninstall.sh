#!/bin/sh
exec "$(dirname "$0")/runtime/python/bin/python3" -I "$(dirname "$0")/install.py" --uninstall "$@"
