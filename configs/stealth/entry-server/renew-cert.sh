#!/bin/bash
# Продление сертификата Let's Encrypt и копирование на exit-сервер
# Установить в cron: 0 3 1 */2 * root /usr/local/bin/renew-cert.sh >> /var/log/renew-cert.log 2>&1
#
# Замените:
#   DOMAIN — домен entry-сервера
#   SSH_KEY — путь к SSH-ключу
#   EXIT_USER — пользователь на exit-сервере
#   EXIT_IP — IP exit-сервера

DOMAIN="your-entry-server.example.com"
SSH_KEY="/home/user1/.ssh/id_ed25519"
EXIT_USER="ubuntu"
EXIT_IP="<EXIT_SERVER_IP>"

# Продление (certbot останавливает туннель чтобы занять порт 80)
certbot renew --quiet --standalone \
    --pre-hook "systemctl stop ssh-tunnel-mtproto" \
    --post-hook "systemctl start ssh-tunnel-mtproto"

# Копирование на exit-сервер
scp -i "$SSH_KEY" "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" "$EXIT_USER@$EXIT_IP:/tmp/entry-server_fullchain.pem"
scp -i "$SSH_KEY" "/etc/letsencrypt/live/$DOMAIN/privkey.pem" "$EXIT_USER@$EXIT_IP:/tmp/entry-server_privkey.pem"
ssh -i "$SSH_KEY" "$EXIT_USER@$EXIT_IP" "\
    sudo mv /tmp/entry-server_fullchain.pem /etc/ssl/entry-server_fullchain.pem && \
    sudo mv /tmp/entry-server_privkey.pem /etc/ssl/entry-server_privkey.pem && \
    sudo chmod 600 /etc/ssl/entry-server_privkey.pem && \
    sudo systemctl reload nginx"
