#!/usr/bin/env python3
"""Deep MTProto proxy checker via FakeTLS handshake.

Performs a real FakeTLS handshake as a Telegram MTProto client would,
verifying the proxy recognizes the secret and responds with a correctly
signed ServerHello. This detects cases where the proxy is up but broken
(e.g. the mtg v2.2.4 bug that routed everything to domain-fronting).

Protocol (based on mtprotoproxy and mtg implementations):

  Client ClientHello:
    - bytes[11:43]  = 32-byte "random" field that actually contains:
                      HMAC-SHA256(secret, ClientHello_with_random_zeroed)
                      XOR (00..00 + current_unix_time_LE)
    - SNI extension with the domain embedded in the secret

  Server ServerHello (correct secret):
    - bytes[11:43]  = 32-byte digest that equals:
                      HMAC-SHA256(secret, client_digest + ServerHello_with_digest_zeroed)

  Server response (wrong secret / unrecognised):
    - Real TLS handshake from the domain-fronting web server
      (digest at the same position is just random TLS server_random, no HMAC match)

Exit: "OK" (0) or error message (1).

Supports both mtprotoproxy and mtg as the target proxy — works with any
FakeTLS-mode MTProto proxy.

Usage:
    python3 check_mtproto_proxy.py "tg://proxy?server=...&port=...&secret=..."
"""

import argparse
import base64
import hashlib
import hmac
import secrets as py_secrets
import socket
import struct
import sys
import time
from urllib.parse import urlparse, unquote


TIMEOUT = 10

# Protocol constants (match mtprotoproxy.py)
TLS_VERS = b"\x03\x03"
DIGEST_LEN = 32
DIGEST_POS = 11           # offset of 32-byte "random" field inside the ClientHello TCP stream
SESSION_ID_LEN_POS = DIGEST_POS + DIGEST_LEN  # 43


def parse_tg_link(link):
    """Parse tg://proxy?server=...&port=...&secret=... link."""
    parsed = urlparse(link)
    params = {}
    for pair in parsed.query.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            params[k] = unquote(v)
    return params["server"], int(params["port"]), params["secret"]


def parse_secret(secret_str):
    """Parse secret, return (16-byte key, domain).

    Accepts:
      - hex:             ee<32 hex chars><domain in hex>
      - base64:          standard or url-safe, with or without padding
    """
    if len(secret_str) > 2 and all(c in "0123456789abcdefABCDEF" for c in secret_str):
        raw = bytes.fromhex(secret_str)
    else:
        s = secret_str.replace("-", "+").replace("_", "/")
        s += "=" * (-len(s) % 4)
        raw = base64.b64decode(s)

    if not raw or raw[0] != 0xEE:
        raise ValueError("Not a FakeTLS secret (expected 0xEE prefix)")
    if len(raw) < 17:
        raise ValueError("Secret too short")

    key = raw[1:17]
    domain = raw[17:].decode("ascii", errors="replace")
    return key, domain


def build_sni_extension(domain):
    """Build the TLS server_name (SNI) extension."""
    sni = domain.encode("ascii")
    inner = b"\x00" + struct.pack(">H", len(sni)) + sni  # name_type=host_name + length + bytes
    inner_list = struct.pack(">H", len(inner)) + inner   # server_name_list length + entry
    return b"\x00\x00" + struct.pack(">H", len(inner_list)) + inner_list


def build_client_hello(domain, random_field):
    """Build a complete TLS 1.3 ClientHello record.

    `random_field` is the 32-byte value that will occupy bytes [11..43]
    of the resulting TCP stream (where the server looks for the digest).
    """
    session_id = py_secrets.token_bytes(32)

    # Cipher suites: just TLS 1.3 AES-128-GCM (matches what mtprotoproxy expects)
    cipher_suites = b"\x13\x01\x13\x02\x13\x03\xc0\x2b\xc0\x2f\xc0\x2c\xc0\x30\xcc\xa9\xcc\xa8\xc0\x13\xc0\x14\x00\x9c\x00\x9d\x00\x2f\x00\x35"

    # Extensions
    sni_ext = build_sni_extension(domain)

    # extended_master_secret
    ems_ext = b"\x00\x17\x00\x00"
    # renegotiation_info
    reneg_ext = b"\xff\x01\x00\x01\x00"
    # supported_groups (curves): x25519, secp256r1, secp384r1
    sup_grp_ext = b"\x00\x0a\x00\x08\x00\x06\x00\x1d\x00\x17\x00\x18"
    # ec_point_formats
    ec_pf_ext = b"\x00\x0b\x00\x02\x01\x00"
    # session_ticket (empty)
    ticket_ext = b"\x00\x23\x00\x00"
    # application_layer_protocol_negotiation (h2, http/1.1)
    alpn = b"\x02h2\x08http/1.1"
    alpn_ext = b"\x00\x10" + struct.pack(">H", len(alpn) + 2) + struct.pack(">H", len(alpn)) + alpn
    # status_request
    status_ext = b"\x00\x05\x00\x05\x01\x00\x00\x00\x00"
    # signature_algorithms
    sig_algs = b"\x04\x03\x08\x04\x04\x01\x05\x03\x08\x05\x05\x01\x08\x06\x06\x01\x02\x01"
    sig_algs_ext = b"\x00\x0d" + struct.pack(">H", len(sig_algs) + 2) + struct.pack(">H", len(sig_algs)) + sig_algs
    # signed_certificate_timestamp
    sct_ext = b"\x00\x12\x00\x00"
    # key_share (x25519 with random public key)
    x25519_pub = py_secrets.token_bytes(32)
    key_share_entry = b"\x00\x1d" + struct.pack(">H", len(x25519_pub)) + x25519_pub
    key_share_list = struct.pack(">H", len(key_share_entry)) + key_share_entry
    key_share_ext = b"\x00\x33" + struct.pack(">H", len(key_share_list)) + key_share_list
    # psk_key_exchange_modes
    psk_kem_ext = b"\x00\x2d\x00\x02\x01\x01"
    # supported_versions (TLS 1.3 + TLS 1.2)
    sup_ver_ext = b"\x00\x2b\x00\x05\x04\x03\x04\x03\x03"
    # compress_certificate
    compress_cert_ext = b"\x00\x1b\x00\x03\x02\x00\x02"

    extensions = (
        sni_ext
        + ems_ext
        + reneg_ext
        + sup_grp_ext
        + ec_pf_ext
        + ticket_ext
        + alpn_ext
        + status_ext
        + sig_algs_ext
        + sct_ext
        + key_share_ext
        + psk_kem_ext
        + sup_ver_ext
        + compress_cert_ext
    )

    # Pad to make handshake look like a typical browser (~517 bytes total)
    # padding extension (type 0x0015) fills up to target length
    handshake_body_so_far = (
        TLS_VERS
        + random_field
        + bytes([len(session_id)]) + session_id
        + struct.pack(">H", len(cipher_suites)) + cipher_suites
        + b"\x01\x00"  # compression methods: 1 method, null
        + struct.pack(">H", len(extensions) + 0)  # placeholder
    )
    # We want record total ~= 517 (common browser ClientHello length)
    # Record = 5 (record hdr) + 4 (handshake hdr) + body
    # Body   = 2 (legacy_version) + 32 (random) + 1+32 (sid) + 2+len(cs) + 2 (comp) + 2+len(ext)
    current_total = 5 + 4 + 2 + 32 + 1 + 32 + 2 + len(cipher_suites) + 2 + 2 + len(extensions)
    pad_needed = max(0, 517 - current_total - 4)  # minus padding ext header
    padding_ext = b"\x00\x15" + struct.pack(">H", pad_needed) + b"\x00" * pad_needed
    extensions += padding_ext

    # Final body
    body = (
        TLS_VERS
        + random_field
        + bytes([len(session_id)]) + session_id
        + struct.pack(">H", len(cipher_suites)) + cipher_suites
        + b"\x01\x00"
        + struct.pack(">H", len(extensions)) + extensions
    )

    # Handshake header: type=1 (ClientHello), 3-byte length
    handshake = b"\x01" + struct.pack(">I", len(body))[1:] + body

    # TLS record header: type=22 (handshake), legacy version 0x0301, 2-byte length
    # NOTE: record version is TLS 1.0 (0x0301), not 0x0303 — mtprotoproxy
    # rejects anything else at TLS_START_BYTES[0:3] = b"\x16\x03\x01"
    record = b"\x16\x03\x01" + struct.pack(">H", len(handshake)) + handshake
    return record


def compute_client_digest(secret_key, hello_with_zero_digest):
    """HMAC-SHA256 over the ClientHello that has its random field zeroed."""
    return hmac.new(secret_key, hello_with_zero_digest, hashlib.sha256).digest()


def receive_tls_record(sock):
    """Read exactly one TLS record from sock. Returns (type, version, payload)."""
    hdr = b""
    while len(hdr) < 5:
        chunk = sock.recv(5 - len(hdr))
        if not chunk:
            return None
        hdr += chunk
    rtype = hdr[0]
    version = hdr[1:3]
    length = struct.unpack(">H", hdr[3:5])[0]
    payload = b""
    while len(payload) < length:
        chunk = sock.recv(length - len(payload))
        if not chunk:
            return None
        payload += chunk
    return rtype, version, payload


def check_proxy(server, port, secret_str):
    try:
        secret_key, domain = parse_secret(secret_str)
    except Exception as e:
        return False, f"Invalid secret: {e}"

    # Step 1: build ClientHello with random = zero, compute its layout
    zero_random = b"\x00" * DIGEST_LEN
    hello_with_zero_random = build_client_hello(domain, zero_random)

    # Step 2: compute HMAC(secret, hello_with_zero_random)
    client_hmac = compute_client_digest(secret_key, hello_with_zero_random)

    # Step 3: xor last 4 bytes with current timestamp (little-endian)
    ts = int(time.time()).to_bytes(4, "little")
    xor_mask = b"\x00" * (DIGEST_LEN - 4) + ts
    random_field = bytes(client_hmac[i] ^ xor_mask[i] for i in range(DIGEST_LEN))

    # Step 4: rebuild the real ClientHello with the computed random_field.
    # The structure (lengths, extensions, session_id) must be identical, so
    # we construct it deterministically: same session_id + same padding.
    # Easier approach: surgically replace the random bytes in hello_with_zero_random.
    real_hello = bytearray(hello_with_zero_random)
    real_hello[DIGEST_POS:DIGEST_POS + DIGEST_LEN] = random_field
    real_hello = bytes(real_hello)

    # Step 5: connect + send
    try:
        sock = socket.create_connection((server, port), timeout=TIMEOUT)
    except socket.timeout:
        return False, f"TCP connect timeout to {server}:{port}"
    except Exception as e:
        return False, f"TCP connect failed to {server}:{port}: {e}"

    try:
        sock.settimeout(TIMEOUT)
        sock.sendall(real_hello)

        # Step 6: read server response. In a real FakeTLS dialogue the server
        # sends ServerHello + ChangeCipherSpec + ApplicationData in one or more
        # records. We only need the ServerHello record (handshake type=0x02).
        rec = receive_tls_record(sock)
        if rec is None:
            return False, "Server closed connection after ClientHello"
        rtype, version, payload = rec

        if rtype != 0x16:
            return False, f"Server returned unexpected TLS record type {hex(rtype)} (expected 0x16 handshake)"
        if len(payload) < 4 or payload[0] != 0x02:
            return False, "Server response is not a ServerHello"

        # Full stream as the server sent it for the ServerHello record
        # (record header + payload) — this is what we feed into the server HMAC
        srv_stream = b"\x16" + version + struct.pack(">H", len(payload)) + payload

        # The server's digest is at the same DIGEST_POS=11 inside the stream
        if len(srv_stream) < DIGEST_POS + DIGEST_LEN:
            return False, "Server response too short for digest extraction"
        server_digest = srv_stream[DIGEST_POS:DIGEST_POS + DIGEST_LEN]

        # Step 7: verify server's HMAC.
        # Per mtprotoproxy source:
        #   server_digest = HMAC(secret, client_random_field + hello_pkt_with_digest_zeroed)
        # where hello_pkt is the FULL server response stream (not just ServerHello)
        #
        # We only have the ServerHello record so far. To fully verify we'd need
        # to read the entire server response (including ChangeCipherSpec + AppData).
        # Read everything the server has to offer with a short timeout.
        sock.settimeout(2.0)
        extra = b""
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                extra += chunk
                if len(extra) > 65536:
                    break
        except socket.timeout:
            pass
        except Exception:
            pass

        full_server_pkt = srv_stream + extra

        hello_pkt_zeroed = (
            full_server_pkt[:DIGEST_POS]
            + b"\x00" * DIGEST_LEN
            + full_server_pkt[DIGEST_POS + DIGEST_LEN:]
        )
        expected_digest = hmac.new(
            secret_key,
            random_field + hello_pkt_zeroed,
            hashlib.sha256,
        ).digest()

        if hmac.compare_digest(expected_digest, server_digest):
            return True, "OK"
        return False, (
            "Server response digest does not match expected HMAC — "
            "proxy did NOT recognize the secret (routed to domain fronting fallback)"
        )
    finally:
        try:
            sock.close()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(
        description="Deep MTProto proxy check via FakeTLS handshake"
    )
    parser.add_argument("tg_link", help='tg://proxy?server=...&port=...&secret=...')
    args = parser.parse_args()

    try:
        server, port, secret = parse_tg_link(args.tg_link)
    except Exception as e:
        print(f"Invalid link: {e}")
        sys.exit(1)

    ok, msg = check_proxy(server, port, secret)
    print(msg)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
