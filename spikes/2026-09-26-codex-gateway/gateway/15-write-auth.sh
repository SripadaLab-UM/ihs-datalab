#!/bin/sh
# Reads the mounted key file and writes an nginx include. Never echoes the key.
set -eu
umask 077
mkdir -p /etc/nginx/secret
KEY="$(cat /run/secrets/umgpt_key)"
printf 'proxy_set_header Authorization "Bearer %s";\n' "$KEY" > /etc/nginx/secret/auth.conf
unset KEY
chown root:root /etc/nginx/secret/auth.conf
echo "15-write-auth.sh: auth include written"
