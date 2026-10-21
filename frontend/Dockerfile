# --- dev: the Vite dev server, for docker compose (target: dev) ------------------
FROM node:22.23.3-alpine3.24 AS dev

WORKDIR /app
RUN chown node:node /app
USER node

COPY --chown=node:node package.json package-lock.json .npmrc ./
RUN npm ci --no-audit --no-fund

COPY --chown=node:node . .

EXPOSE 5173
CMD ["npm", "run", "dev"]

# --- build: the static production build --------------------------------------------
FROM dev AS build
RUN npm run build

# --- runtime (default): nginx serves the build and forwards /api to the backend ----
FROM nginxinc/nginx-unprivileged:1.29.8-alpine3.23 AS runtime

# Filled into nginx/default.conf.template at start-up; set API_UPSTREAM to the backend's Service.
ENV API_UPSTREAM=http://backend:8000 \
    CLIENT_MAX_BODY_SIZE=12m

COPY nginx/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/healthz"]
