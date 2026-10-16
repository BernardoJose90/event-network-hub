FROM node:22.23.3-alpine3.24

WORKDIR /app
RUN chown node:node /app
USER node

COPY --chown=node:node package.json package-lock.json .npmrc ./
RUN npm ci --no-audit --no-fund

COPY --chown=node:node . .

EXPOSE 5173
CMD ["npm", "run", "dev"]
