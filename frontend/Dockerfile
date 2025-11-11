# Use nginx to serve static frontend files
FROM nginxinc/nginx-unprivileged:alpine

# Install curl for health checks
USER root
RUN apk add --no-cache curl gettext
USER nginx

# Copy all frontend files to nginx html directory
COPY dashboard.html /usr/share/nginx/html/dashboard.html.template
COPY dashboard.html /usr/share/nginx/html/dashboard.html
COPY index.html /usr/share/nginx/html/

# Create custom nginx config for port 3000
USER root

# Create startup script
RUN echo '#!/bin/sh' > /startup.sh && \
    echo 'echo "API_GATEWAY_URL: $API_GATEWAY_URL"' >> /startup.sh && \
    echo 'envsubst "\$API_GATEWAY_URL" < /usr/share/nginx/html/dashboard.html.template > /usr/share/nginx/html/dashboard.html' >> /startup.sh && \
    echo 'nginx -g "daemon off;"' >> /startup.sh && \
    chmod +x /startup.sh


RUN echo 'server { \
    listen 3000; \
    server_name localhost; \
    root /usr/share/nginx/html; \
    index index.html dashboard.html; \
    location / { \
        try_files $uri $uri/ /index.html; \
    } \
    location /health { \
        access_log off; \
        return 200 "healthy\n"; \
        add_header Content-Type text/plain; \
    } \
}' > /etc/nginx/conf.d/default.conf
USER nginx

EXPOSE 3000

# Start nginx in foreground
CMD ["nginx", "-g", "daemon off;"]
