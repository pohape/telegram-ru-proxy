# MTProto Proxy через SSH-туннель

MTProto Proxy для Telegram с двухсерверной схемой: входной сервер в России принимает подключения клиентов, а выходной сервер за рубежом подключается к Telegram. Между серверами — SSH-туннель, который российские провайдеры не могут отличить от обычного SSH-трафика и замедлить.

## Зачем это нужно

Российские провайдеры умеют распознавать и замедлять VPN-протоколы (WireGuard, OpenVPN) и прямые прокси-подключения с помощью DPI (Deep Packet Inspection). SSH-трафик при этом не трогают — он неотличим от обычного администрирования серверов.

Дополнительно, MTProto Proxy с режимом fake TLS маскирует трафик между Telegram-клиентом и входным сервером под обычный HTTPS (например, к google.com), что делает его невидимым для DPI.

## Схема работы

```
┌──────────┐    fake TLS     ┌──────────────┐   SSH-туннель    ┌──────────────┐          ┌──────────┐
│ Telegram │ ──────────────> │ Entry-сервер │ ═══════════════> │  Exit-сервер │ ───────> │ Telegram │
│  клиент  │   :443          │   (Россия)   │  зашифрованный   │ (Франкфурт)  │          │ серверы  │
└──────────┘                 │              │     канал        │   mtg :8443  │          └──────────┘
                             │  autossh     │                  │              │
                             └──────────────┘                  └──────────────┘
```

**Что видит провайдер:**
- Клиент → Entry-сервер: обычный HTTPS-трафик к google.com (fake TLS)
- Entry-сервер → Exit-сервер: обычный SSH-трафик (администрирование)

**Что не видит провайдер:**
- Что внутри SSH-туннеля передаются данные Telegram
- Что HTTPS-подключение клиента — это на самом деле MTProto

## Компоненты

| Компонент | Где | Что делает |
|-----------|-----|-----------|
| [mtg](https://github.com/9seconds/mtg) | Exit-сервер | MTProto Proxy, подключается к серверам Telegram |
| [autossh](https://www.harding.motd.ca/autossh/) | Entry-сервер | Держит SSH-туннель с автоматическим переподключением |
| SSH-туннель | Entry → Exit | Пробрасывает порт 443 entry-сервера на порт 8443 exit-сервера |

## Выбор серверов

**Entry-сервер (Россия):** рекомендуется [Cloud.ru](https://cloud.ru) — free tier за ~150 руб/мес (оплата только за публичный IP-адрес). Виртуальная машина бесплатна. Минимальной конфигурации достаточно — entry-сервер только пробрасывает трафик через SSH-туннель.

**Exit-сервер (за рубежом):** рекомендуется [Tencent Cloud Lighthouse](https://www.tencentcloud.com/products/lighthouse) — 2 vCPU Linux VPS за ~$10 при оплате за год. Выбирайте регион Frankfurt. Подойдёт минимальная конфигурация — mtg потребляет мало ресурсов.

## Требования

- Два VPS: один в России (entry), один за рубежом (exit)
- SSH-доступ с entry-сервера на exit-сервер по ключу (без пароля)
- Открытый порт 443/TCP на entry-сервере (для клиентов)
- Ubuntu/Debian на обоих серверах (инструкция для apt, но адаптируется под любой дистрибутив)

## Установка

### 1. Настройка SSH-ключа (entry-сервер)

Сгенерировать ключ на entry-сервере и скопировать публичную часть на exit-сервер:

```bash
# На entry-сервере
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N '' -C 'entry->exit tunnel'

# Скопировать публичный ключ на exit-сервер
ssh-copy-id -i ~/.ssh/id_ed25519.pub user@<EXIT_SERVER_IP>
```

Настроить SSH-конфиг на entry-сервере (`~/.ssh/config`):

```
Host exit-server
    HostName <EXIT_SERVER_IP>
    User <USER>
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
    ServerAliveInterval 30
    ServerAliveCountMax 3
```

Проверить подключение:

```bash
ssh exit-server hostname
```

### 2. Установка mtg (exit-сервер)

```bash
# Скачать последний релиз
curl -sL https://github.com/9seconds/mtg/releases/download/v2.2.4/mtg-2.2.4-linux-amd64.tar.gz \
  | tar xz --strip-components=1 -C /tmp
sudo mv /tmp/mtg /usr/local/bin/mtg
sudo chmod +x /usr/local/bin/mtg

# Проверить
mtg --version
```

Сгенерировать секрет с fake TLS (маскировка под google.com):

```bash
mtg generate-secret google.com
# Пример вывода: 7iJvsrJi3VaLeoqPImIjONhnb29nbGUuY29t
```

> Можно указать любой домен вместо google.com — это домен, под HTTPS-трафик к которому будет маскироваться протокол.

Создать конфиг `/etc/mtg.toml`:

```toml
secret = "СЮДА_ВСТАВИТЬ_СГЕНЕРИРОВАННЫЙ_СЕКРЕТ"
bind-to = "127.0.0.1:8443"
```

Создать systemd-сервис — скопировать `configs/exit-server/mtg.service` в `/etc/systemd/system/mtg.service`, затем:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mtg
sudo systemctl status mtg
```

### 3. Установка autossh и SSH-туннеля (entry-сервер)

```bash
sudo apt-get install -y autossh
```

Скопировать `configs/entry-server/ssh-tunnel-mtproto.service` в `/etc/systemd/system/ssh-tunnel-mtproto.service`.

> Перед копированием отредактируйте файл: замените `User=user1` на вашего пользователя и `exit-server` на имя хоста из SSH-конфига.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ssh-tunnel-mtproto
sudo systemctl status ssh-tunnel-mtproto
```

Проверить что туннель работает:

```bash
ss -tlnp | grep :443
# Должен показать ssh, слушающий на 0.0.0.0:443
```

### 4. Открыть порт

Открыть порт **443/TCP** на entry-сервере в панели управления хостинга (security group, файрвол и т.д.).

### 5. Проверить

С внешней машины:

```bash
nc -zv <ENTRY_SERVER_IP> 443
# Connection succeeded = всё работает
```

## Подключение Telegram

Ссылка для добавления прокси в Telegram:

```
tg://proxy?server=<ENTRY_SERVER_DOMAIN>&port=443&secret=<СЕКРЕТ>
```

Или вручную в настройках Telegram:

- **Тип:** MTProto
- **Сервер:** `<ENTRY_SERVER_DOMAIN>`
- **Порт:** `443`
- **Секрет:** `<СЕКРЕТ>`

## Обслуживание

### Проверка статуса

```bash
# На entry-сервере
sudo systemctl status ssh-tunnel-mtproto

# На exit-сервере
sudo systemctl status mtg
```

### Просмотр логов

```bash
# SSH-туннель
sudo journalctl -u ssh-tunnel-mtproto -f

# MTProto Proxy
sudo journalctl -u mtg -f
```

### Перезапуск

```bash
# Перезапуск туннеля (entry-сервер)
sudo systemctl restart ssh-tunnel-mtproto

# Перезапуск mtg (exit-сервер)
sudo systemctl restart mtg
```

### Смена секрета

```bash
# На exit-сервере
mtg generate-secret google.com
# Вписать новый секрет в /etc/mtg.toml
sudo systemctl restart mtg
# Обновить ссылку tg://proxy у клиентов
```

## Устойчивость к перезагрузкам

Все сервисы добавлены в автозагрузку (`systemctl enable`). При перезагрузке:

| Сценарий | Поведение |
|----------|-----------|
| Перезагрузка entry-сервера | autossh стартует автоматически, поднимает туннель |
| Перезагрузка exit-сервера | mtg стартует автоматически. autossh на entry-сервере переподключится в течение 30 сек |
| Оба сервера одновременно | Каждый поднимет свои сервисы. autossh будет пытаться подключиться пока exit-сервер не станет доступен |

## Структура проекта

```
ruproxy/
├── README.md
└── configs/
    ├── entry-server/
    │   ├── ssh-tunnel-mtproto.service   # systemd-сервис SSH-туннеля
    │   └── ssh-config                   # пример SSH-конфига
    └── exit-server/
        ├── mtg.service                  # systemd-сервис mtg
        └── mtg.toml                     # конфиг mtg (шаблон)
```
