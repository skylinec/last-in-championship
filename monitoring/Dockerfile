FROM node:18-alpine

WORKDIR /app

# Add postgresql-client for schema initialization
RUN apk add --no-cache postgresql-client

COPY package*.json ./
RUN npm install

COPY . .

# Add initialization script
COPY docker-entrypoint.sh /
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["npm", "start"]
