FROM node:18-alpine

WORKDIR /usr/src/app

# Install wget for healthcheck
RUN apk add --no-cache wget

# Install dependencies
COPY package*.json ./
RUN npm install --production

# Copy source code
COPY src/ ./src/

# Expose the port the app runs on
EXPOSE 5200

# Start the application
CMD ["node", "src/app.js"]