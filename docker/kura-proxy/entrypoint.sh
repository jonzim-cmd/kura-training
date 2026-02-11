#!/bin/sh
# Substitute only KURA_API_KEY (leave nginx vars like $host untouched)
envsubst '${KURA_API_KEY}' < /etc/nginx/nginx.conf.template > /etc/nginx/conf.d/default.conf
exec nginx -g 'daemon off;'
