#!/bin/bash
# setup-server.sh — kept for old links/docs. The real installer is
# install-daemon.sh; server mode = its --systemd flag.
exec "$(dirname "$0")/install-daemon.sh" --systemd
