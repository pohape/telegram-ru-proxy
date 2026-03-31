#!/usr/bin/env python3
"""Проверка MTProto proxy end-to-end через Telethon.

Подключается к Telegram через MTProto proxy (как реальный клиент).
Если connect() проходит — прокси работает, трафик доходит до Telegram.

Использование:
    python3 check_mtproto_proxy.py --tg-api-id 12345 --tg-api-hash abc123 "tg://proxy?server=...&port=...&secret=..."

    --tg-api-id и --tg-api-hash можно заменить переменными окружения TG_API_ID и TG_API_HASH.
    Получить их можно на https://my.telegram.org (API development tools).

Выход: OK (exit 0) или текст ошибки (exit 1)
"""

import os
import sys
import argparse
import asyncio
import base64
import logging
from urllib.parse import urlparse, parse_qs
from telethon import TelegramClient
from telethon.network import ConnectionTcpMTProxyRandomizedIntermediate

logging.disable(logging.CRITICAL)

TIMEOUT = 15


def parse_tg_link(link):
    """Парсит tg://proxy?server=...&port=...&secret=... ссылку."""
    parsed = urlparse(link)
    params = parse_qs(parsed.query)
    server = params["server"][0]
    port = int(params["port"][0])
    secret_b64 = params["secret"][0]
    return server, port, secret_b64


def secret_to_dd(secret_b64):
    """Конвертирует base64-секрет из tg://proxy ссылки в dd-формат для Telethon."""
    raw = base64.b64decode(secret_b64)
    secret_16 = raw[1:17]
    return "dd" + secret_16.hex()


async def check_proxy(server, port, dd_secret, api_id, api_hash):
    client = TelegramClient(
        "/tmp/mtproto_check_session",
        api_id,
        api_hash,
        connection=ConnectionTcpMTProxyRandomizedIntermediate,
        proxy=(server, port, dd_secret),
        timeout=TIMEOUT,
        connection_retries=1,
        retry_delay=1,
        auto_reconnect=False,
    )

    try:
        await asyncio.wait_for(client.connect(), timeout=TIMEOUT)
        if client.is_connected():
            await client.disconnect()
            return True, "OK"
        else:
            return False, f"Не удалось подключиться через {server}:{port}"
    except asyncio.TimeoutError:
        return False, f"Таймаут {TIMEOUT}с через {server}:{port}"
    except Exception as e:
        return False, f"Ошибка через {server}:{port}: {e}"
    finally:
        if client.is_connected():
            await client.disconnect()


def main():
    parser = argparse.ArgumentParser(description="Проверка MTProto proxy end-to-end")
    parser.add_argument("tg_link", help='tg://proxy?server=...&port=...&secret=...')
    parser.add_argument("--tg-api-id", default=os.environ.get("TG_API_ID"),
                        help="Telegram API ID (или TG_API_ID env)")
    parser.add_argument("--tg-api-hash", default=os.environ.get("TG_API_HASH"),
                        help="Telegram API Hash (или TG_API_HASH env)")
    args = parser.parse_args()

    if not args.tg_api_id or not args.tg_api_hash:
        print("Не заданы --tg-api-id и --tg-api-hash (или TG_API_ID/TG_API_HASH)")
        sys.exit(1)

    server, port, secret_b64 = parse_tg_link(args.tg_link)
    dd_secret = secret_to_dd(secret_b64)

    ok, msg = asyncio.run(check_proxy(server, port, dd_secret, int(args.tg_api_id), args.tg_api_hash))
    print(msg)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
