# MTProto Proxy для Telegram через SSH-туннель

MTProto Proxy для Telegram с двухсерверной схемой: entry-сервер в России принимает подключения клиентов, а exit-сервер за рубежом подключается к Telegram. Между серверами — SSH-туннель, который российские провайдеры не могут отличить от обычного SSH-трафика и замедлить.

## Зачем это нужно

Российские провайдеры умеют распознавать и замедлять VPN-протоколы (WireGuard, OpenVPN) и прямые прокси-подключения с помощью DPI. SSH-трафик при этом не трогают — он неотличим от обычного администрирования серверов.

MTProto Proxy с режимом fake TLS дополнительно маскирует трафик между Telegram-клиентом и entry-сервером под обычный HTTPS, что делает его невидимым для DPI.

## Два способа установки

| | Простой | Stealth |
|---|---------|---------|
| **Сложность** | 15 минут | 30 минут |
| **Компоненты на entry** | autossh | autossh, certbot, cron |
| **Компоненты на exit** | mtg | mtg, nginx, TLS-сертификат |
| **Маскировка** | fake TLS (SNI вашего домена) | fake TLS + реальный сайт на домене |
| **Проверка РКН** | Браузер получит ошибку | Браузер увидит реальный сайт |
| **Открытые порты на entry** | 443/TCP | 443/TCP, 80/TCP |
| **Устойчивость к DPI** | Средняя | Высокая |
| **Рекомендация** | Для личного использования | Для раздачи другим людям |

---

## Общая часть: подготовка серверов

### Выбор серверов

**Entry-сервер (Россия):** рекомендуется [Cloud.ru](https://cloud.ru) — бесплатная VM, оплата только за публичный IP (~150 руб/мес). Минимальной конфигурации достаточно — entry-сервер только пробрасывает трафик.

**Exit-сервер (за рубежом):** любой VPS в Европе. Например, [Tencent Cloud Lighthouse](https://www.tencentcloud.com/products/lighthouse) — 2 vCPU Linux VPS за ~$10/год (доступен Frankfurt). Минимальной конфигурации достаточно — mtg потребляет мало ресурсов.

### Требования

- Два VPS: один в России (entry), один за рубежом (exit)
- Ubuntu/Debian на обоих серверах
- Домен, направленный на IP entry-сервера (A-запись)

### 1. Настройка SSH-ключа (entry-сервер)

```bash
# На entry-сервере
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N '' -C 'entry->exit tunnel'

# Скопировать публичный ключ на exit-сервер
ssh-copy-id -i ~/.ssh/id_ed25519.pub user@<EXIT_SERVER_IP>
```

Настроить `~/.ssh/config` на entry-сервере (шаблон: `configs/*/entry-server/ssh-config`):

```
Host exit-server
    HostName <EXIT_SERVER_IP>
    User <USER>
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
    ServerAliveInterval 30
    ServerAliveCountMax 3
```

Проверить: `ssh exit-server hostname`

### 2. Установка mtg (exit-сервер)

```bash
curl -sL https://github.com/9seconds/mtg/releases/download/v2.2.4/mtg-2.2.4-linux-amd64.tar.gz \
  | tar xz --strip-components=1 -C /tmp
sudo mv /tmp/mtg /usr/local/bin/mtg
sudo chmod +x /usr/local/bin/mtg
mtg --version
```

### 3. Генерация секрета

```bash
# Замените на домен вашего entry-сервера
mtg generate-secret your-entry-server.example.com
```

> Всегда используйте домен вашего entry-сервера. Это домен, который будет виден в SNI TLS-подключения. Использование чужих доменов (google.com и т.п.) — подозрительно для DPI, так как IP-адрес entry-сервера не соответствует домену.

### 4. Установка autossh (entry-сервер)

```bash
sudo apt-get install -y autossh
```

---

## Способ 1: Простой

Минимальная установка — только mtg и SSH-туннель. При обращении к домену через браузер сайт не откроется (ошибка подключения).

### Схема

```
                       Entry-сервер (Россия)              Exit-сервер (за рубежом)

Telegram ── :443 ──>  [ autossh -L :443 → :8443 ]  ══>  [ mtg :8443 ]  ──>  Telegram
клиент    fake TLS           SSH-туннель                  MTProto Proxy       серверы
```

**Что видит провайдер:**
- Клиент → Entry: HTTPS-трафик к вашему домену (fake TLS)
- Entry → Exit: обычный SSH-трафик

### Конфигурация exit-сервера

Скопировать `configs/simple/exit-server/mtg.toml` в `/etc/mtg.toml`, вписать секрет.

Скопировать `configs/simple/exit-server/mtg.service` в `/etc/systemd/system/mtg.service`.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mtg
```

### Конфигурация entry-сервера

Скопировать `configs/simple/entry-server/ssh-tunnel-mtproto.service` в `/etc/systemd/system/ssh-tunnel-mtproto.service`. Заменить `User=user1` на вашего пользователя и `exit-server` на хост из SSH-конфига.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ssh-tunnel-mtproto
```

### Открыть порт

Открыть **443/TCP** на entry-сервере в панели управления хостингом (security group, файрвол и т.д.).

> **Cloud.ru:** по умолчанию все входящие порты закрыты. Необходимо в веб-интерфейсе перейти в раздел «Группы безопасности», создать новую группу и добавить правило входящего трафика (ingress) для порта **443/TCP** с источником `0.0.0.0/0`. Затем привязать эту группу к виртуальной машине. Применение правил может занять несколько минут.

### Проверить

```bash
# С внешней машины
nc -zv <ENTRY_SERVER_IP> 443
# Connection succeeded = работает
```

---

## Способ 2: Stealth (с маскировкой под сайт)

Полная маскировка — при обращении к домену через браузер открывается реальный сайт. MTProto-клиенты обслуживаются mtg, остальные подключения перенаправляются на nginx с сайтом-заглушкой (domain fronting).

### Схема

```
                       Entry-сервер (Россия)              Exit-сервер (за рубежом)

Telegram ── :443 ──>  [ autossh -L :443 → :3443 ]  ══>  [ mtg :3443 ] ──> Telegram
клиент    fake TLS           SSH-туннель                       │             серверы
                                                               │
Браузер ─── :443 ──>  [ autossh -L :443 → :3443 ]  ══>  [ mtg :3443 ]
РКН                          SSH-туннель                       │
                                                               ↓
                                                         [ nginx :8080 ]
                                                           сайт-заглушка
```

**Что видит провайдер:**
- Клиент → Entry: обычный HTTPS-трафик к вашему домену
- Entry → Exit: обычный SSH-трафик

**Что видит РКН при проверке:**
- Реальный HTTPS-сайт с валидным сертификатом

### Конфигурация exit-сервера

#### nginx (сайт-заглушка)

```bash
sudo apt-get install -y nginx
```

Разместить HTML-страницу в `/var/www/html/index.html` (любой правдоподобный контент).

Скопировать `configs/stealth/exit-server/nginx-fallback.conf` в `/etc/nginx/sites-available/fallback`. Заменить `server_name` и пути к сертификатам.

```bash
sudo ln -sf /etc/nginx/sites-available/fallback /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

#### mtg

Скопировать `configs/stealth/exit-server/mtg.toml` в `/etc/mtg.toml`, вписать секрет.

Скопировать `configs/stealth/exit-server/mtg.service` в `/etc/systemd/system/mtg.service`.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mtg
```

### Конфигурация entry-сервера

#### Сертификат Let's Encrypt

```bash
sudo apt-get install -y certbot

# Порт 80 должен быть открыт и свободен
sudo certbot certonly --standalone -d your-entry-server.example.com \
    --non-interactive --agree-tos -m your@email.com
```

Скопировать сертификат на exit-сервер:

```bash
scp /etc/letsencrypt/live/<DOMAIN>/fullchain.pem user@<EXIT_IP>:/tmp/entry-server_fullchain.pem
scp /etc/letsencrypt/live/<DOMAIN>/privkey.pem user@<EXIT_IP>:/tmp/entry-server_privkey.pem
ssh user@<EXIT_IP> "sudo mv /tmp/entry-server_fullchain.pem /etc/ssl/ && \
    sudo mv /tmp/entry-server_privkey.pem /etc/ssl/ && \
    sudo chmod 600 /etc/ssl/entry-server_privkey.pem && \
    sudo systemctl reload nginx"
```

#### Автопродление сертификата

Скопировать `configs/stealth/entry-server/renew-cert.sh` в `/usr/local/bin/renew-cert.sh`, заменить переменные.

```bash
sudo chmod +x /usr/local/bin/renew-cert.sh
echo '0 3 1 */2 * root /usr/local/bin/renew-cert.sh >> /var/log/renew-cert.log 2>&1' \
    | sudo tee /etc/cron.d/renew-cert
```

#### SSH-туннель

Скопировать `configs/stealth/entry-server/ssh-tunnel-mtproto.service` в `/etc/systemd/system/ssh-tunnel-mtproto.service`. Заменить `User=user1` и `exit-server`.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ssh-tunnel-mtproto
```

### Открыть порты

Открыть на entry-сервере в панели управления хостингом (security group, файрвол и т.д.):
- **443/TCP** — для клиентов и HTTPS
- **80/TCP** — для выпуска и продления сертификата Let's Encrypt

> **Cloud.ru:** по умолчанию все входящие порты закрыты. Необходимо в веб-интерфейсе перейти в раздел «Группы безопасности», создать новую группу и добавить правила входящего трафика (ingress) для портов **443/TCP** и **80/TCP** с источником `0.0.0.0/0`. Затем привязать эту группу к виртуальной машине. Применение правил может занять несколько минут.

### Проверить

```bash
# Порт доступен
nc -zv <ENTRY_SERVER_IP> 443

# Сайт-заглушка работает через всю цепочку
curl -sk https://your-entry-server.example.com | head
```

---

## Подключение Telegram

```
tg://proxy?server=<ДОМЕН_ENTRY_СЕРВЕРА>&port=443&secret=<СЕКРЕТ>
```

Или вручную: Настройки → Данные и хранилище → Прокси → Добавить прокси:

- **Тип:** MTProto
- **Сервер:** `<ДОМЕН_ENTRY_СЕРВЕРА>`
- **Порт:** `443`
- **Секрет:** `<СЕКРЕТ>`

## Обслуживание

```bash
# Статус (entry-сервер)
sudo systemctl status ssh-tunnel-mtproto

# Статус (exit-сервер)
sudo systemctl status mtg

# Логи
sudo journalctl -u ssh-tunnel-mtproto -f    # entry
sudo journalctl -u mtg -f                   # exit

# Перезапуск
sudo systemctl restart ssh-tunnel-mtproto   # entry
sudo systemctl restart mtg                  # exit
```

### Смена секрета

```bash
# На exit-сервере — замените домен на свой
mtg generate-secret your-entry-server.example.com
# Вписать новый секрет в /etc/mtg.toml
sudo systemctl restart mtg
# Обновить ссылку tg://proxy у клиентов
```

## Устойчивость к перезагрузкам

Все сервисы добавлены в автозагрузку (`systemctl enable`).

| Сценарий | Поведение |
|----------|-----------|
| Перезагрузка entry-сервера | autossh стартует автоматически, поднимает туннель |
| Перезагрузка exit-сервера | mtg стартует автоматически. autossh на entry переподключится в течение 30 сек |
| Оба сервера одновременно | Каждый поднимет свои сервисы. autossh будет пытаться подключиться пока exit-сервер не станет доступен |

## Структура проекта

```
telegram-ru-proxy/
├── README.md
└── configs/
    ├── simple/                          # Способ 1: простой
    │   ├── entry-server/
    │   │   ├── ssh-tunnel-mtproto.service
    │   │   └── ssh-config
    │   └── exit-server/
    │       ├── mtg.service
    │       └── mtg.toml
    └── stealth/                         # Способ 2: с маскировкой
        ├── entry-server/
        │   ├── ssh-tunnel-mtproto.service
        │   ├── ssh-config
        │   └── renew-cert.sh
        └── exit-server/
            ├── mtg.service
            ├── mtg.toml
            └── nginx-fallback.conf
```
