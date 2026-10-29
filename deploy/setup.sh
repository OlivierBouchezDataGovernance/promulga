#!/bin/sh
# Prepares a Promulga installation in the current folder (run it from the project root):
#   - creates .env from .env.example if it does not exist,
#   - creates secrets/ with one file per secret, readable by its owner only,
#   - generates the OnlyOffice secret,
#   - gives the files to uid 1000 (the user of the Promulga container) if needed.
# Safe to run again: existing files are kept.
set -eu
cd "$(dirname "$0")/.."

umask 077
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env - edit it now (SITE_DOMAIN, BASE_URL, ADMIN_EMAIL...)."
fi
chmod 600 .env

mkdir -p secrets
chmod 700 secrets
for name in smtp_password github_token oidc_client_secret onlyoffice_jwt_secret signing_cert_password; do
  [ -f "secrets/$name" ] || : > "secrets/$name"
done
if [ ! -s secrets/onlyoffice_jwt_secret ]; then
  head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > secrets/onlyoffice_jwt_secret
fi
chmod 600 secrets/*

# The containers read the secrets as uid 1000: hand the files over if you are another user.
if [ "$(id -u)" != "1000" ]; then
  echo "Your uid is $(id -u); the Promulga container runs as uid 1000."
  echo "Giving secrets/ to uid 1000 (sudo may ask for your password)..."
  sudo chown -R 1000:1000 secrets
fi

echo "Secrets folder ready. Put each secret alone in its file, for example:"
echo "  nano secrets/smtp_password      (SMTP or app password)"
echo "  nano secrets/github_token       (fine-grained token for the public export)"
echo "Then check everything with: ./deploy/check.sh"
