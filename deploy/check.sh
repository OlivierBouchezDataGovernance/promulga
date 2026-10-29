#!/bin/sh
# Checks, on the server, what the application cannot see from inside its container:
# permissions of .env and secrets/, and passwords left in .env.
# Run it from the project root:  ./deploy/check.sh
cd "$(dirname "$0")/.." || exit 1
status=0
ok()   { printf '  [ OK ]  %s\n' "$1"; }
bad()  { printf '  [FIX ]  %s\n' "$1"; status=1; }
info() { printf '  [INFO]  %s\n' "$1"; }

mode() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"; }

echo "Promulga installation checks"
if [ -f .env ]; then
  m=$(mode .env)
  case "$m" in 600|400) ok ".env is readable by its owner only ($m)";; *) bad ".env has mode $m: run 'chmod 600 .env'";; esac
  if grep -Eq '^ADMIN_PASSWORD=.+' .env; then
    bad "ADMIN_PASSWORD is set in .env: remove it once the administrator has changed the password"
  else ok "no administrator password in .env"; fi
  if grep -Eq '^SECRET_KEY=.+' .env; then
    bad "SECRET_KEY is in .env: remove the line (a random key is generated and kept in the data volume)"
  fi
  for var in SMTP_PASSWORD GITHUB_TOKEN OIDC_CLIENT_SECRET ONLYOFFICE_JWT_SECRET SIGNING_CERT_PASSWORD; do
    if grep -Eq "^${var}=.+" .env; then bad "$var is in .env: move it to secrets/$(echo $var | tr 'A-Z' 'a-z')"; fi
  done
  grep -Eq '^SECURE_COOKIES=true' .env && ok "secure cookies" || info "SECURE_COOKIES is not true (fine for a local HTTP test only)"
else
  bad ".env is missing: run ./deploy/setup.sh"
fi

if [ -d secrets ]; then
  m=$(mode secrets)
  case "$m" in 700|500) ok "secrets/ is private ($m)";; *) bad "secrets/ has mode $m: run 'chmod 700 secrets'";; esac
  for f in secrets/*; do
    [ -f "$f" ] || continue
    m=$(mode "$f")
    case "$m" in 600|400) ok "$f ($m)";; *) bad "$f has mode $m: run 'chmod 600 $f'";; esac
  done
else
  bad "secrets/ is missing: run ./deploy/setup.sh"
fi

if git ls-files --error-unmatch .env secrets >/dev/null 2>&1; then
  bad ".env or secrets/ is tracked by git: remove it from the repository"
else ok ".env and secrets/ are not tracked by git"; fi

[ $status -eq 0 ] && echo "All checks passed." || echo "Some points need fixing (see [FIX ])."
exit $status
