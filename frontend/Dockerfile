FROM node:22-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.30-alpine
RUN apk add --no-cache openssl
COPY --from=build /src/dist /usr/share/nginx/html
COPY docker/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY docker/30-self-signed-cert.sh docker/40-runtime-env.sh /docker-entrypoint.d/
RUN chmod +x /docker-entrypoint.d/30-self-signed-cert.sh /docker-entrypoint.d/40-runtime-env.sh
EXPOSE 80 443
