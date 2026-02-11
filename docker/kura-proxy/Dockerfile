FROM nginx:alpine

RUN rm /etc/nginx/conf.d/default.conf

COPY nginx.conf.template /etc/nginx/nginx.conf.template
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8320

ENTRYPOINT ["/entrypoint.sh"]
