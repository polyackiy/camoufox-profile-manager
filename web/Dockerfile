# Frontend image: Next.js web UI. Build from the repository root:
#   docker build -f web/Dockerfile -t camoufox-pm-web .
FROM node:20-slim AS build
WORKDIR /app
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
# The API proxy target is baked at build time (Next.js rewrites are static).
# In docker-compose this points at the "api" service.
ARG API_PROXY_TARGET=http://api:8000
ENV API_PROXY_TARGET=$API_PROXY_TARGET
RUN npm run build

FROM node:20-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app ./
EXPOSE 3000
CMD ["npm", "start"]
