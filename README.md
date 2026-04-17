# MTProto Proxy для Telegram через SSH-туннель

MTProto Proxy для Telegram с двухсерверной схемой: entry-сервер в России принимает подключения клиентов, а exit-сервер за рубежом подключается к Telegram. Между серверами — SSH-туннель, который не могут отличить от обычного SSH-трафика и замедлить.

## Зачем это нужно

РКН умеет распознавать и замедлять VPN-протоколы (WireGuard, OpenVPN) и прямые прокси-подключения с помощью DPI. SSH-трафик при этом не трогают — он неотличим от обычного администрирования серверов.

MTProto Proxy с режимом fake TLS дополнительно маскирует трафик между Telegram-клиентом и entry-сервером под обычный HTTPS. При открытии домена через браузер отображается настоящий сайт-заглушка — проверяющий увидит обычный личный сайт, а не ошибку.

В качестве MTProto-демона используется [**mtg**](https://github.com/9seconds/mtg). Выбран из-за активной разработки (релизы практически каждую неделю, регулярные фиксы против DPI-детекта) и встроенной поддержки FakeTLS + domain-fronting из одного бинарника.

## Схема

```
┌──────────┐    fake TLS     ┌──────────────┐   SSH-туннель    ┌────────────────────┐          ┌──────────┐
│ Telegram │ ──────────────> │ Entry-сервер │ ═══════════════> │    Exit-сервер     │ ───────> │ Telegram │
│  клиент  │   :443          │   (Россия)   │  зашифрованный   │      mtg :3443     │          │ серверы  │
└──────────┘                 │              │     канал        │         │          │          └──────────┘
                             │  autossh     │                  │         \/         │
┌──────────┐    HTTPS        │              │                  │    nginx :8080     │
│ Браузер  │ ──────────────> │              │ ═══════════════> │   сайт-заглушка    │
│   РКН    │   :443          │              │                  │                    │
└──────────┘                 └──────────────┘                  └────────────────────┘
```

**Что видит провайдер:**
- Клиент → Entry: обычный HTTPS-трафик к вашему домену
- Entry → Exit: обычный SSH-трафик

**Что видит РКН при проверке через браузер:**
- Реальный HTTPS-сайт с валидным сертификатом Let's Encrypt

---

## Требования

- Два VPS: один в России (entry), один за рубежом (exit)
- Ubuntu/Debian на обоих серверах
- Домен, направленный на IP entry-сервера (A-запись)
- Открытые порты **443/TCP** и **80/TCP** на entry-сервере

## Выбор серверов

**Entry-сервер (Россия):** рекомендуется [Cloud.ru](https://cloud.ru) — бесплатная VPS, оплата только за публичный IP (~150 руб/мес). Минимальной конфигурации достаточно — entry-сервер только пробрасывает трафик.

**Exit-сервер (за рубежом):** любой VPS в Европе. Например, [Tencent Cloud Lighthouse](https://www.tencentcloud.com/products/lighthouse) — 2 vCPU Linux VPS за ~$10/год (доступен Frankfurt). Минимальной конфигурации достаточно — mtg потребляет мало ресурсов (~10 МБ RAM).

---

## Установка

Инструкция предполагает, что вы работаете **от root** или через `sudo`. Все команды выполняются в указанной последовательности — сначала весь entry-сервер, потом весь exit-сервер (нужно чтобы сертификат уже был выпущен к моменту настройки nginx/mtg).

### Шаг 0а. Открыть порты на хостинге

**Это критичный шаг — без него certbot в шаге 4 не сработает.** Откройте в панели управления хостингом на **entry-сервере**:

- **443/TCP** — для клиентов Telegram и HTTPS
- **80/TCP** — для выпуска и продления сертификата Let's Encrypt

На exit-сервере дополнительных портов открывать не нужно — входящие соединения туда идут только через SSH (22/TCP, обычно открыт по умолчанию).

> **Cloud.ru:** по умолчанию все входящие порты закрыты. В веб-интерфейсе перейдите в раздел «Группы безопасности», создайте новую группу и добавьте правила входящего трафика (ingress) для портов **443/TCP** и **80/TCP** с источником `0.0.0.0/0`. Затем привяжите эту группу к виртуальной машине. Применение правил может занять несколько минут.

### Шаг 0б. Установить зависимости

На каждом сервере установите пакеты. Наборы разные: на entry-сервере всё для туннеля и выпуска сертификата, на exit-сервере — только `git` и `nginx` (остальное притащится в шаге 10 — туда mtg скачивается как бинарник, не через apt).

**На entry-сервере:**
```bash
apt-get update
apt-get install -y git certbot autossh dnsutils
```

**На exit-сервере:**
```bash
apt-get update
apt-get install -y git nginx
```

### Шаг 0в. Клонировать репозиторий на оба сервера

Все конфиги нужны на каждом сервере. Клонируйте в `/opt`:

```bash
# На entry-сервере И на exit-сервере
cd /opt
git clone https://github.com/pohape/telegram-ru-proxy.git
```

---

## Entry-сервер (Россия)

### Шаг 1. Сгенерировать SSH-ключ для подключения к exit-серверу

Этот ключ используется autossh чтобы держать SSH-туннель без пароля.

```bash
# На entry-сервере
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N '' -C 'entry->exit tunnel'
```

### Шаг 2. Скопировать публичный ключ на exit-сервер

```bash
ssh-copy-id -i ~/.ssh/id_ed25519.pub root@<EXIT_SERVER_IP>
```

> **Важно про имя пользователя.** Многие cloud-провайдеры (Tencent Lighthouse, AWS, Hetzner Cloud) по умолчанию **запрещают** SSH от root и создают для вас готового пользователя — чаще всего `ubuntu`, иногда `debian` или `ec2-user`. Если `ssh-copy-id root@...` падает с «Permission denied» — подставьте имя пользователя вашего провайдера: `ssh-copy-id ubuntu@...`. Уточнить дефолтного юзера можно в панели хостера или в документации образа.
>
> Это имя пользователя нужно будет запомнить — оно попадёт в SSH-конфиг (Шаг 3), в scp-команды (Шаг 5) и в `renew-cert.sh` (Шаг 6).

### Шаг 3. Настроить SSH-алиас для удобства

```bash
EXIT_IP=203.0.113.5      # ← замените на IP вашего exit-сервера
EXIT_USER=root           # ← замените на пользователя с Шага 2 (часто ubuntu, debian и т.п.)

cat >> ~/.ssh/config << EOF

Host exit-server
    HostName $EXIT_IP
    User $EXIT_USER
    Port 22
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
    ServerAliveInterval 30
    ServerAliveCountMax 3
EOF
chmod 600 ~/.ssh/config
```

> Если SSH на exit-сервере слушает на нестандартном порту (некоторые хостеры так делают) — замените `Port 22` на реальный порт.

Проверьте подключение:
```bash
ssh exit-server hostname
```

Должно вывести hostname exit-сервера без запроса пароля.

### Шаг 4. Выпустить SSL-сертификат Let's Encrypt

Порт **80** должен быть открыт (см. Шаг 0а) и свободен — никакие веб-серверы не должны висеть на нём.

Сначала убедитесь что DNS уже распространился — A-запись должна возвращать IP entry-сервера:

```bash
dig +short entry.example.ru
# должен вернуть IP entry-сервера; если нет — подождите несколько минут
```

Затем выпустите сертификат:

```bash
certbot certonly --standalone -d entry.example.ru \
    --non-interactive --agree-tos -m your@email.com
```

Замените `entry.example.ru` и email на свои. После успешного выпуска сертификат окажется в `/etc/letsencrypt/live/entry.example.ru/`.

### Шаг 5. Скопировать сертификат на exit-сервер

Копируем сначала в `/tmp` (туда может писать любой пользователь), потом `sudo mv` в `/etc/ssl/`. Такая схема работает и для root, и для non-root пользователя с passwordless sudo (типичный дефолт на Tencent / AWS / Hetzner).

```bash
DOMAIN=entry.example.ru   # ← замените на ваш домен
scp /etc/letsencrypt/live/$DOMAIN/fullchain.pem exit-server:/tmp/entry-server_fullchain.pem
scp /etc/letsencrypt/live/$DOMAIN/privkey.pem exit-server:/tmp/entry-server_privkey.pem
ssh exit-server "sudo mv /tmp/entry-server_fullchain.pem /etc/ssl/ && \
                 sudo mv /tmp/entry-server_privkey.pem /etc/ssl/ && \
                 sudo chmod 600 /etc/ssl/entry-server_privkey.pem"
```

### Шаг 6. Настроить автопродление сертификата

```bash
cp /opt/telegram-ru-proxy/configs/entry-server/renew-cert.sh /usr/local/bin/renew-cert.sh
chmod +x /usr/local/bin/renew-cert.sh
```

Откройте `/usr/local/bin/renew-cert.sh` в редакторе и замените 4 переменные в начале файла:

- `DOMAIN` — ваш домен (например `entry.example.ru`)
- `SSH_KEY` — путь к приватному SSH-ключу (обычно `/root/.ssh/id_ed25519`)
- `EXIT_USER` — пользователь на exit-сервере (тот же, что вы использовали в Шаге 2)
- `EXIT_IP` — IP exit-сервера

Добавьте cron-запись (продление раз в 2 месяца, 1-го числа в 03:00):
```bash
echo '0 3 1 */2 * root /usr/local/bin/renew-cert.sh >> /var/log/renew-cert.log 2>&1' \
    | tee /etc/cron.d/renew-cert > /dev/null
```

### Шаг 7. Настроить SSH-туннель через systemd

```bash
cp /opt/telegram-ru-proxy/configs/entry-server/ssh-tunnel-mtproto.service \
    /etc/systemd/system/ssh-tunnel-mtproto.service
```

Откройте `/etc/systemd/system/ssh-tunnel-mtproto.service` в редакторе:

- Если у вас есть отдельный пользователь — замените `User=user1` на него.
- Если вы работаете под root и больше никого нет — замените на `User=root` (или просто удалите эту строку — systemd по умолчанию запустит от root).
- Замените `exit-server` на имя хоста из SSH-конфига, если вы его называли иначе.

Запустите и включите автозагрузку:
```bash
systemctl daemon-reload
systemctl enable --now ssh-tunnel-mtproto
systemctl status ssh-tunnel-mtproto
```

Проверьте что порт 443 слушает:
```bash
ss -tlnp | grep ':443 '
```

> На этом этапе туннель стоит, но на exit-сервере ещё не настроен mtg (это шаги 8–13). Если сейчас откроете `https://entry.example.ru` в браузере — получите ошибку «connection reset». Это нормально, всё заработает после настройки nginx+mtg в шагах 8–13.

---

## Exit-сервер (за рубежом)

Теперь переключитесь на exit-сервер:

```bash
# С entry-сервера
ssh exit-server
```

### Шаг 8. Разместить сайт-заглушку

В репо лежит готовый шаблон. Скопируйте его:

```bash
cp /opt/telegram-ru-proxy/configs/exit-server/index.html /var/www/html/index.html
```

Откройте `/var/www/html/index.html` и отредактируйте содержимое — замените заголовок «Мои любимые рецепты» и сами рецепты на что-нибудь ваше. Главное чтобы сайт выглядел как настоящий личный блог/визитка, а не как свежий сервер с `It works!`.

### Шаг 9. Настроить nginx vhost

```bash
cp /opt/telegram-ru-proxy/configs/exit-server/nginx-fallback.conf \
    /etc/nginx/sites-available/fallback
```

Откройте `/etc/nginx/sites-available/fallback` и замените **только `server_name`** на ваш домен. Пути к сертификатам в шаблоне (`/etc/ssl/entry-server_fullchain.pem` и `/etc/ssl/entry-server_privkey.pem`) уже совпадают с тем, куда вы скопировали их в шаге 5 — менять не надо.

```bash
ln -sf /etc/nginx/sites-available/fallback /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
systemctl enable nginx
```

`nginx -t` должен вернуть «syntax is ok» — если ругается на отсутствующий сертификат, значит вы пропустили шаг 5.

### Шаг 10. Установить mtg

Скачайте последний релиз [mtg](https://github.com/9seconds/mtg/releases) под Linux amd64:

```bash
# Замените MTG_VERSION на актуальную версию с https://github.com/9seconds/mtg/releases
MTG_VERSION=2.2.8
curl -sL "https://github.com/9seconds/mtg/releases/download/v${MTG_VERSION}/mtg-${MTG_VERSION}-linux-amd64.tar.gz" \
    | tar xz --strip-components=1 -C /tmp
mv /tmp/mtg /usr/local/bin/mtg
chmod +x /usr/local/bin/mtg
mtg --version
```

### Шаг 11. Сгенерировать секрет

```bash
mtg generate-secret entry.example.ru
```

Замените домен на ваш. На выходе получите строку вида `7iem4kIaSCdUXV/geqhhNHRlbnRyeS5leGFtcGxlLnJ1` — это полный секрет в base64. **Сохраните его** — он пойдёт и в конфиг mtg, и в ссылку `tg://proxy?...&secret=...` для клиентов.

> Всегда подставляйте в `generate-secret` **домен вашего entry-сервера**, а не чужой (`google.com` и подобные). Домен зашит внутри секрета и используется как SNI при TLS-подключении. Если SNI = `google.com`, а IP-адрес сервера принадлежит Cloud.ru или Beget — DPI это легко палит.

### Шаг 12. Настроить mtg

```bash
cp /opt/telegram-ru-proxy/configs/exit-server/mtg.toml /etc/mtg.toml
```

Откройте `/etc/mtg.toml` и замените `YOUR_SECRET_HERE` на секрет из шага 11.

В конфиге также настроена секция `[domain-fronting]` с `ip = "127.0.0.1"` и `port = 8080` — это заставляет mtg отправлять не-MTProto подключения на локальный nginx (иначе mtg резолвит домен из секрета через DNS, попадает обратно на entry-сервер — получается петля).

### Шаг 13. Запустить mtg через systemd

```bash
cp /opt/telegram-ru-proxy/configs/exit-server/mtg.service /etc/systemd/system/mtg.service
systemctl daemon-reload
systemctl enable --now mtg
systemctl status mtg
```

---

## Проверить

**С внешней машины:**

```bash
# Порт доступен
nc -zv entry.example.ru 443

# Сайт-заглушка открывается через всю цепочку
curl -sk https://entry.example.ru | grep '<title>'
```

**На exit-сервере (диагностика mtg):**

```bash
mtg doctor /etc/mtg.toml
```

Проверяет валидность конфига, расхождение времени, доступность серверов Telegram, достижимость fronting-домена. В двухсерверной схеме SNI-DNS-mismatch (красная галочка про SNI) — **ожидаемо**: домен указывает на entry-сервер, а mtg крутится на exit-сервере. Это не ошибка, всё остальное должно быть зелёное.

**MTProto-проверка через наш скрипт (с любой машины):**

```bash
python3 /opt/telegram-ru-proxy/check_mtproto_proxy.py "tg://proxy?server=entry.example.ru&port=443&secret=ВАШ_СЕКРЕТ"
```

Должно вывести `OK`. Если `Server response digest does not match expected HMAC` — значит прокси не распознал секрет и отправил вас на fallback-сайт (например, секрет в ссылке не совпадает с `/etc/mtg.toml`).

---

## Подключение Telegram

```
tg://proxy?server=<ДОМЕН_ENTRY_СЕРВЕРА>&port=443&secret=<СЕКРЕТ>
```

`<СЕКРЕТ>` — это та самая строка, которую вывел `mtg generate-secret <домен>` (в формате base64). В ссылку подставляется **точно как есть**, без дополнительного кодирования.

Или вручную: Настройки → Данные и хранилище → Прокси → Добавить прокси:

- **Тип:** MTProto
- **Сервер:** `<ДОМЕН_ENTRY_СЕРВЕРА>`
- **Порт:** `443`
- **Секрет:** `<СЕКРЕТ>`

---

## Обслуживание

```bash
# Статус (entry-сервер)
systemctl status ssh-tunnel-mtproto

# Статус (exit-сервер)
systemctl status mtg

# Логи
journalctl -u ssh-tunnel-mtproto -f    # entry
journalctl -u mtg -f                    # exit

# Перезапуск
systemctl restart ssh-tunnel-mtproto   # entry
systemctl restart mtg                   # exit
```

### Обновление mtg

mtg обновляется часто — полезно следить за новыми релизами и обновляться, особенно если в changelog упомянуты фиксы DPI.

```bash
# На exit-сервере
MTG_VERSION=2.2.8  # ← заменить на актуальную версию с https://github.com/9seconds/mtg/releases
curl -sL "https://github.com/9seconds/mtg/releases/download/v${MTG_VERSION}/mtg-${MTG_VERSION}-linux-amd64.tar.gz" | tar xz --strip-components=1 -C /tmp
mv /tmp/mtg /usr/local/bin/mtg
chmod +x /usr/local/bin/mtg
systemctl restart mtg
mtg --version
```

### Смена секрета

```bash
# На exit-сервере
mtg generate-secret entry.example.ru
# Вписать новый секрет в /etc/mtg.toml
systemctl restart mtg
# Обновить ссылку tg://proxy у клиентов
```

---

## Устойчивость к перезагрузкам

Все сервисы добавлены в автозагрузку (`systemctl enable`).

| Сценарий | Поведение |
|----------|-----------|
| Перезагрузка entry-сервера | autossh стартует автоматически, поднимает туннель |
| Перезагрузка exit-сервера | mtg и nginx стартуют автоматически. autossh на entry переподключится в течение 30 сек |
| Оба сервера одновременно | Каждый поднимет свои сервисы. autossh будет пытаться подключиться пока exit-сервер не станет доступен |

---

## Мониторинг

В комплекте идёт скрипт `check_mtproto_proxy.py` — глубокая проверка MTProto-прокси, которая подтверждает, что сервер действительно обслуживает FakeTLS MTProto-клиентов, а не просто принимает TCP-соединения. Именно такую проверку не получается сделать простым `curl`-ом: страница-заглушка может открываться, TCP-порт отвечать, но Telegram при этом не работать (например, когда прокси по какой-то причине отправляет все подключения на fallback-сайт вместо MTProto).

### Что именно проверяет

Скрипт выполняет настоящий FakeTLS handshake, как это делает Telegram-клиент:

1. Формирует TLS 1.3 ClientHello со структурой, которую ждёт mtg (длина ≥ 517 байт, TLS record version `0x0301`, SNI с доменом из секрета).
2. Вычисляет 32-байтный `HMAC-SHA256(секрет, ClientHello с зануленным random-полем)`, XOR-ит последние 4 байта с текущим unix-временем и подставляет результат в поле random.
3. Отправляет ClientHello на сервер и читает ответ.
4. Извлекает из ответа 32-байтный серверный digest (поле `random` в ServerHello).
5. Вычисляет ожидаемое значение: `HMAC-SHA256(секрет, клиентский_digest + ServerHello_с_зануленным_digest)`.
6. Сравнивает. Совпадение = сервер знает секрет и корректно обслужил MTProto-клиента. Несовпадение = запрос ушёл в domain fronting (fallback), то есть прокси не узнал клиента.

За счёт HMAC-проверки скрипт отличает работающий MTProto-прокси от «мёртвого» прокси, который просто показывает сайт-заглушку любому входящему.

### Зависимости

Только стандартная библиотека Python 3 (`hmac`, `hashlib`, `socket`, `struct`, `secrets`, `base64`, `urllib`, `argparse`).

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
    command: "python3 /opt/telegram-ru-proxy/check_mtproto_proxy.py 'tg://proxy?server=your-server.example.ru&port=443&secret=YOUR_SECRET'"
    search_string: "OK"
    timeout: 15
    schedule: '*/5 * * * *'
    tg_chats_to_notify:
      - 123456789
    notify_after_attempt: 2
```

При падении прокси вы получите уведомление в Telegram, а при восстановлении — уведомление о восстановлении с указанием длительности даунтайма.

> 🇷🇺 Если мониторинг крутится в России и провайдер блокирует `api.telegram.org` — настройте в монитор-тулзе опцию `telegram_proxy` (SOCKS5 через SSH-туннель). См. его README, секция «Telegram Proxy».

---

## Структура проекта

```
telegram-ru-proxy/
├── README.md
├── check_mtproto_proxy.py               # Скрипт мониторинга (FakeTLS handshake)
└── configs/
    ├── entry-server/
    │   ├── ssh-tunnel-mtproto.service   # systemd-юнит SSH-туннеля
    │   └── renew-cert.sh                # скрипт продления сертификата
    └── exit-server/
        ├── mtg.toml                     # конфиг mtg
        ├── mtg.service                  # systemd-юнит mtg
        ├── nginx-fallback.conf          # nginx vhost для сайта-заглушки
        └── index.html                   # шаблон сайта-заглушки (рецепты)
```
