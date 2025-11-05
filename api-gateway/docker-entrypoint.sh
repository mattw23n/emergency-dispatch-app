#!/usr/bin/env sh
set -eu

envsubst '${insurance_service_url_internal} ${notification_service_url_internal} ${dispatch_service_url_internal} ${billings_service_url_internal} ${events_manager_service_url_internal} ${triage_service_url_internal} ${wearable_service_url_internal} ${events_stream_service_url_internal}' < /etc/nginx/conf.d/default.conf.template > /etc/nginx/conf.d/default.conf

exec "$@"