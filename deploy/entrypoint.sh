#!/bin/sh
# Container entry point.
# Docker secrets keep the owner and mode of the files on the host (secrets/*, mode 600),
# so the unprivileged app user may not be able to read them. Started as root, this
# script copies them to a private tmpfs folder owned by the app user, points the
# *_FILE variables there, then drops root privileges for good.
set -eu
APP_USER=promulga

if [ "$(id -u)" = "0" ]; then
  if [ -d /run/secrets ]; then
    mkdir -p /dev/shm/promulga-secrets
    chmod 700 /dev/shm/promulga-secrets
    for f in /run/secrets/*; do
      [ -f "$f" ] || continue
      cp "$f" "/dev/shm/promulga-secrets/$(basename "$f")"
    done
    chown -R "$APP_USER:$APP_USER" /dev/shm/promulga-secrets
    chmod 600 /dev/shm/promulga-secrets/* 2>/dev/null || true
    for var in $(env | sed -n 's/^\([A-Z_]*_FILE\)=\/run\/secrets\/.*/\1/p'); do
      eval "value=\${$var}"
      export "$var=/dev/shm/promulga-secrets/$(basename "$value")"
    done
  fi
  chown "$APP_USER:$APP_USER" /data
  exec setpriv --reuid="$APP_USER" --regid="$APP_USER" --init-groups "$@"
fi
exec "$@"
