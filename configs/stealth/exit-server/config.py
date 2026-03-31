# mtprotoproxy config с маскировкой (exit-сервер)
# https://github.com/alexbers/mtprotoproxy

PORT = 3443

USERS = {
    # Замените секрет на свой (32 hex-символа)
    # Можно сгенерировать: python3 -c "import secrets; print(secrets.token_hex(16))"
    "tg": "00000000000000000000000000000001",
}

MODES = {
    "classic": False,
    "secure": False,
    "tls": True
}

# Замените на домен вашего entry-сервера
TLS_DOMAIN = "your-entry-server.example.com"

# Не-MTProto клиенты (браузер, проверка РКН) перенаправляются на локальный nginx
MASK_HOST = "127.0.0.1"
MASK_PORT = 8080
