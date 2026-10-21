FROM kong:3.9.1

COPY deploy/kong/kong.yml.tmpl /kong/kong.yml.tmpl
COPY --chmod=0755 deploy/kong/render.sh /kong/render.sh

ENV PORT=8000
EXPOSE 8000
ENTRYPOINT ["/kong/render.sh"]
