FROM nginx:1.29.2

# Copy nginx configuration template
COPY ./api-gateway/nginx/nginx.conf.template /etc/nginx/conf.d/default.conf.template

# Copy entrypoint script
COPY api-gateway/docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

# Copy frontend files for Kubernetes deployment
# Note: Build this from project root with: docker build -f api-gateway/Dockerfile .
COPY frontend /usr/share/nginx/html

# nosemgrep: dockerfile.security.missing-user-entrypoint.missing-user-entrypoint
ENTRYPOINT ["/docker-entrypoint.sh"]

# nosemgrep: dockerfile.security.missing-user.missing-user
CMD ["nginx", "-g", "daemon off;"]
