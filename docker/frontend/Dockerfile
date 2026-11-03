# Фронтенд: файлы из frontend/ как есть + nginx. Собирается из корня проекта (context: .)
FROM alpine:3.22 AS build
WORKDIR /site
COPY frontend/static ./static
COPY frontend/templates/index.html ./index.html.tpl
# index.html — шаблон Django: {% static '…' %} → /static/…, {{ v }} → время сборки (браузер не берёт старое из кэша)
RUN v=$(date +%s) && sed \
      -e "s/{% *load static *%}//" \
      -e "s#{% *static '\([^']*\)' *%}#/static/\1#g" \
      -e "s/{{ *v *}}/$v/g" \
      index.html.tpl > index.html && rm index.html.tpl && ! grep -q "{%\|{{" index.html

FROM nginx:1.29-alpine
COPY docker/frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /site /usr/share/nginx/html
