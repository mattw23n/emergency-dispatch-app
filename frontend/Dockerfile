# Use nginx to serve static frontend files
FROM nginx:alpine

# Install curl for health checks
RUN apk add --no-cache curl

# Copy all frontend files to nginx html directory
COPY dashboard.html /usr/share/nginx/html/
COPY index.html /usr/share/nginx/html/
# Create custom nginx config for port 3000
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

RUN chown -R nginx:nginx /var/cache/nginx /var/run /var/log/nginx && \
    chmod -R 755 /var/cache/nginx && \
    chmod -R 755 /var/run && \
    chmod -R 755 /var/log/nginx

EXPOSE 3000

# Run nginx as non-root user
USER nginx

# Start nginx in foreground
CMD ["nginx", "-g", "daemon off;"]
