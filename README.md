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
| **Компоненты на exit** | mtprotoproxy | mtprotoproxy, nginx, TLS-сертификат |
| **Маскировка** | fake TLS (SNI вашего домена) | fake TLS + реальный сайт на домене |
| **Проверка РКН** | Браузер получит ошибку | Браузер увидит реальный сайт |
| **Открытые порты на entry** | 443/TCP | 443/TCP, 80/TCP |
| **Устойчивость к DPI** | Средняя | Высокая |
| **Рекомендация** | Для личного использования | Для раздачи другим людям |

---

## Общая часть: подготовка серверов

### Выбор серверов

**Entry-сервер (Россия):** рекомендуется [Cloud.ru](https://cloud.ru) — бесплатная VM, оплата только за публичный IP (~150 руб/мес). Минимальной конфигурации достаточно — entry-сервер только пробрасывает трафик.

**Exit-сервер (за рубежом):** любой VPS в Европе. Например, [Tencent Cloud Lighthouse](https://www.tencentcloud.com/products/lighthouse) — 2 vCPU Linux VPS за ~$10/год (доступен Frankfurt). Минимальной конфигурации достаточно — mtprotoproxy потребляет мало ресурсов.

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

### 2. Установка mtprotoproxy (exit-сервер)

```bash
cd /opt
sudo git clone https://github.com/alexbers/mtprotoproxy.git
```

### 3. Генерация секрета

```bash
# Сгенерировать 16-байтный hex-секрет
python3 -c "import secrets; print(secrets.token_hex(16))"
```

Секрет для Telegram-ссылки формируется так: `ee` + hex-секрет + hex-кодировка домена:

```bash
# Пример для домена your-entry-server.example.com и секрета abcdef...
echo -n "your-entry-server.example.com" | xxd -p
# Результат: ссылка tg://proxy?server=...&port=443&secret=ee<секрет><hex-домен>
```

> Всегда используйте домен вашего entry-сервера. Это домен, который будет виден в SNI TLS-подключения. Использование чужих доменов (google.com и т.п.) — подозрительно для DPI, так как IP-адрес entry-сервера не соответствует домену.

### 4. Установка autossh (entry-сервер)

```bash
sudo apt-get install -y autossh
```

---

## Способ 1: Простой

Минимальная установка — только mtprotoproxy и SSH-туннель. При обращении к домену через браузер сайт не откроется (ошибка подключения).

### Схема

```
┌──────────┐    fake TLS     ┌──────────────┐   SSH-туннель    ┌────────────────────┐          ┌──────────┐
│ Telegram │ ──────────────> │ Entry-сервер │ ═══════════════> │    Exit-сервер     │ ───────> │ Telegram │
│  клиент  │   :443          │   (Россия)   │  зашифрованный   │   (Франкфурт)      │          │ серверы  │
└──────────┘                 │              │     канал        │ mtprotoproxy :8443 │          └──────────┘
                             │  autossh     │                  │                    │
                             └──────────────┘                  └────────────────────┘
```

**Что видит провайдер:**
- Клиент → Entry: HTTPS-трафик к вашему домену (fake TLS)
- Entry → Exit: обычный SSH-трафик

### Конфигурация exit-сервера

Скопировать `configs/simple/exit-server/config.py` в `/opt/mtprotoproxy/config.py`, вписать секрет и домен.

Скопировать `configs/simple/exit-server/mtprotoproxy.service` в `/etc/systemd/system/mtprotoproxy.service`.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mtprotoproxy
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

Полная маскировка — при обращении к домену через браузер открывается реальный сайт. MTProto-клиенты обслуживаются mtprotoproxy, остальные подключения перенаправляются на nginx с сайтом-заглушкой.

### Схема

```
┌──────────┐    fake TLS     ┌──────────────┐   SSH-туннель    ┌────────────────────┐          ┌──────────┐
│ Telegram │ ──────────────> │ Entry-сервер │ ═══════════════> │    Exit-сервер     │ ───────> │ Telegram │
│  клиент  │   :443          │   (Россия)   │  зашифрованный   │ mtprotoproxy :3443 │          │ серверы  │
└──────────┘                 │              │     канал        │        │           │          └──────────┘
                             │  autossh     │                  │        ↓          │
┌──────────┐    HTTPS        │              │                  │   nginx :8080      │
│ Браузер  │ ──────────────> │              │ ═══════════════> │   сайт-заглушка    │
│   РКН    │   :443          │              │                  │                    │
└──────────┘                 └──────────────┘                  └────────────────────┘
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

#### mtprotoproxy

Скопировать `configs/stealth/exit-server/config.py` в `/opt/mtprotoproxy/config.py`, вписать секрет и домен.

Скопировать `configs/stealth/exit-server/mtprotoproxy.service` в `/etc/systemd/system/mtprotoproxy.service`.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mtprotoproxy
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

Секрет для ссылки формируется из hex-секрета в config.py:
```
ee<hex-секрет-из-config.py><hex-домена>
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
sudo systemctl status mtprotoproxy

# Логи
sudo journalctl -u ssh-tunnel-mtproto -f    # entry
sudo journalctl -u mtprotoproxy -f           # exit

# Перезапуск
sudo systemctl restart ssh-tunnel-mtproto   # entry
sudo systemctl restart mtprotoproxy          # exit
```

### Смена секрета

```bash
# На exit-сервере
python3 -c "import secrets; print(secrets.token_hex(16))"
# Вписать новый секрет в /opt/mtprotoproxy/config.py
sudo systemctl restart mtprotoproxy
# Обновить ссылку tg://proxy у клиентов
```

## Устойчивость к перезагрузкам

Все сервисы добавлены в автозагрузку (`systemctl enable`).

| Сценарий | Поведение |
|----------|-----------|
| Перезагрузка entry-сервера | autossh стартует автоматически, поднимает туннель |
| Перезагрузка exit-сервера | mtprotoproxy стартует автоматически. autossh на entry переподключится в течение 30 сек |
| Оба сервера одновременно | Каждый поднимет свои сервисы. autossh будет пытаться подключиться пока exit-сервер не станет доступен |

## Мониторинг

В комплекте идёт скрипт `check_mtproto_proxy.py` — глубокая проверка MTProto-прокси, которая подтверждает, что сервер действительно обслуживает FakeTLS MTProto-клиентов, а не просто принимает TCP-соединения. Именно такую проверку не получается сделать простым `curl`-ом: страница-заглушка может открываться, TCP-порт отвечать, но Telegram при этом не работать (например, когда прокси по какой-то причине отправляет все подключения на fallback-сайт вместо MTProto).

### Что именно проверяет

Скрипт выполняет настоящий FakeTLS handshake, как это делает Telegram-клиент:

1. Формирует TLS 1.3 ClientHello со структурой, которую ждёт mtg / mtprotoproxy (длина ≥ 517 байт, TLS record version `0x0301`, SNI с доменом из секрета).
2. Вычисляет 32-байтный `HMAC-SHA256(секрет, ClientHello с зануленным random-полем)`, XOR-ит последние 4 байта с текущим unix-временем и подставляет результат в поле random.
3. Отправляет ClientHello на сервер и читает ответ.
4. Извлекает из ответа 32-байтный серверный digest (поле `random` в ServerHello).
5. Вычисляет ожидаемое значение: `HMAC-SHA256(секрет, клиентский_digest + ServerHello_с_зануленным_digest)`.
6. Сравнивает. Совпадение = сервер знает секрет и корректно обслужил MTProto-клиента. Несовпадение = запрос ушёл в domain fronting (fallback), то есть прокси не узнал клиента.

За счёт HMAC-проверки скрипт отличает работающий MTProto-проксик от «мёртвого» прокси, который просто показывает сайт-заглушку любому входящему.

### Совместимость

- ✅ **mtg** (FakeTLS mode) — единственный режим, который поддерживает mtg
- ✅ **mtprotoproxy** с `tls: True` в `config.py`
- ❌ Старые режимы (`classic`, `secure`/dd-secret) — у FakeTLS-проверки другая логика handshake

### Зависимости

Только стандартная библиотека Python 3 (`hmac`, `hashlib`, `socket`, `struct`, `secrets`, `base64`, `urllib`, `argparse`). Никаких `pip install` не требуется, не нужно `api_id`/`api_hash` и аккаунт Telegram.

### Использование

```bash
python3 check_mtproto_proxy.py "tg://proxy?server=...&port=...&secret=..."
```

Скрипт принимает полную `tg://proxy` ссылку — ту же, что раздаётся пользователям. Секрет распознаётся в любом из трёх форматов:

- **hex:** `ee<32 hex>...` (напр. `ee418622effe42a41b1ff4a28341079a6e68656c6c6f64656e69732e7275`)
- **base64:** стандартный с `+` и `/`
- **base64 url-safe:** с `-` и `_`

### Возвращаемые значения

| Результат | stdout | exit code |
|-----------|--------|-----------|
| Прокси работает (HMAC совпал) | `OK` | `0` |
| Прокси не узнал секрет (ушёл в fallback) | `Server response digest does not match expected HMAC — proxy did NOT recognize the secret (routed to domain fronting fallback)` | `1` |
| TCP/SSL не прошли | `TCP connect failed to server:port: ...` / `TCP connect timeout` | `1` |
| Сервер закрыл соединение | `Server closed connection after ClientHello` | `1` |
| Сломанный секрет | `Invalid secret: ...` | `1` |

### Интеграция с мониторингом

Скрипт спроектирован для использования с [self-hosted-tg-alerts-uptime-monitor](https://github.com/pohape/self-hosted-tg-alerts-uptime-monitor) — self-hosted системой мониторинга с уведомлениями в Telegram. Она умеет мониторить сайты, выполнять shell-команды, проверять SSL-сертификаты, отправлять алерты при падении и уведомления при восстановлении, а также генерировать периодические отчёты.

Пример конфигурации в `config.yaml`:

```yaml
commands:
  mtproto_proxy_1:
    command: "python3 /path/to/check_mtproto_proxy.py 'tg://proxy?server=your-server.example.com&port=443&secret=YOUR_SECRET'"
    search_string: "OK"
    timeout: 15
    schedule: '*/5 * * * *'
    tg_chats_to_notify:
      - 123456789
    notify_after_attempt: 2
```

При падении прокси вы получите уведомление в Telegram, а при восстановлении — уведомление о восстановлении с указанием длительности даунтайма.

> 🇷🇺 Если мониторинг крутится в России и провайдер блокирует `api.telegram.org` — настройте в монитор-тулзе опцию `telegram_proxy` (SOCKS5 через SSH-туннель). См. его README, секция «Telegram Proxy».

## Структура проекта

```
telegram-ru-proxy/
├── README.md
├── check_mtproto_proxy.py           # Скрипт мониторинга (end-to-end проверка)
└── configs/
    ├── simple/                          # Способ 1: простой
    │   ├── entry-server/
    │   │   ├── ssh-tunnel-mtproto.service
    │   │   └── ssh-config
    │   └── exit-server/
    │       ├── mtprotoproxy.service
    │       └── config.py
    └── stealth/                             # Способ 2: с маскировкой
        ├── entry-server/
        │   ├── ssh-tunnel-mtproto.service
        │   ├── ssh-config
        │   └── renew-cert.sh
        └── exit-server/
            ├── mtprotoproxy.service
            ├── config.py
            └── nginx-fallback.conf
```
