# Use nginx to serve static frontend files
FROM nginx:alpine

# Copy all frontend files to nginx html directory
COPY dashboard.html /usr/share/nginx/html/
COPY index.html /usr/share/nginx/html/
COPY README.md /usr/share/nginx/html/

# Create non-root user for security
RUN adduser -D -H -u 1001 -s /sbin/nologin nginxuser

# Change nginx to run on port 8080 (non-privileged port)
RUN sed -i 's/listen\s*80;/listen 8080;/' /etc/nginx/conf.d/default.conf && \
    chown -R nginxuser:nginxuser /var/cache/nginx && \
    chown -R nginxuser:nginxuser /var/log/nginx && \
    chown -R nginxuser:nginxuser /etc/nginx/conf.d && \
    touch /var/run/nginx.pid && \
    chown -R nginxuser:nginxuser /var/run/nginx.pid

USER nginxuser

# Expose port 8080 (non-privileged)
EXPOSE 8080

# Start nginx
CMD ["nginx", "-g", "daemon off;"]
