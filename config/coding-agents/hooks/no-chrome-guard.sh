#!/bin/bash
HERE=${BASH_SOURCE[0]%/*}
exec /usr/bin/python3 -I "$HERE/no_chrome_guard.py"
