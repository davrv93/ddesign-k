# Panel Qwik: build estático + nginx que sirve el sitio y enruta /api y /media al backend.
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
ARG PUBLIC_URL=https://kddesign.pjgfactsalud.com.pe
ENV PUBLIC_URL=${PUBLIC_URL}
# Ruta base del panel: "/" o, detrás de un alias de nginx, p. ej. "/baruka/". JMD Ventas (multiempresa) usa
# "/jmdventas/XEMPRESAX/": su nginx (nginx.jmdventas.conf) cambia el marcador por la empresa de la ruta.
ARG BASE_PATH=/
ENV BASE_PATH=${BASE_PATH}
# Marca del producto en los títulos («Baruka Design» o «JMD Ventas»); el nombre de la empresa lo da el backend.
ARG MARCA=
ENV MARCA=${MARCA}
RUN npm run build \
 # Con ruta base, Qwik deja el sitio en dist/<base>/: se sube a la raíz, porque el alias de nginx
 # quita el prefijo antes de llegar a este contenedor.
 && if [ "$BASE_PATH" != "/" ]; then mv "dist/${BASE_PATH#/}" /tmp/site && rm -rf dist && mv /tmp/site dist; fi

FROM nginx:1.29-alpine
# nginx.conf (una tienda) o nginx.jmdventas.conf (multiempresa).
ARG NGINX_CONF=nginx.conf
COPY ${NGINX_CONF} /etc/nginx/conf.d/default.conf
COPY jmdventas/index.html /usr/share/nginx/jmd/index.html
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD wget -qO- http://127.0.0.1/healthz || exit 1
