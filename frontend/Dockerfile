# Use nginx to serve static frontend files
FROM nginx:alpine

# Copy all frontend files to nginx html directory
COPY dashboard.html /usr/share/nginx/html/
COPY index.html /usr/share/nginx/html/
COPY README.md /usr/share/nginx/html/

# Copy custom nginx configuration if needed
# COPY nginx.conf /etc/nginx/conf.d/default.conf

# Expose port 80
EXPOSE 80

# Start nginx
CMD ["nginx", "-g", "daemon off;"]
