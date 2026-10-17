"""
Criptografia no cliente: o destino (server ou repositório Git) só guarda dados
cifrados e não consegue ler nem as chaves de API, nem o nome dos arquivos.

  SYNC_KEY=<32 bytes em base64>   igual em TODOS os PCs (python src/components/remote_client.py synckey)

Com SYNC_KEY:
  - conteúdo e manifesto: deflate + AES-256-GCM (nonce aleatório, AAD = id do blob)
  - id de cada blob: HMAC-SHA256(chave, conteúdo), que não revela o conteúdo,
    nem permite confirmar se um arquivo conhecido está lá
Sem SYNC_KEY: só deflate, e o id é o sha256 do conteúdo (modo antigo, sem sigilo).

As duas chaves (cifra e id) saem da SYNC_KEY por HKDF, então uma não revela a outra.
"""

import base64
import hashlib
import hmac
import os
import zlib

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAX_PLAIN = 200 * 1024 * 1024
ENCRYPTED = b"E1"
PLAIN = b"P1"


class CodecError(Exception):
    pass


def _derive(key: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(key)


def new_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def _deflate(data: bytes) -> bytes:
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    return c.compress(data) + c.flush()


def _inflate(data: bytes, limit: int) -> bytes:
    d = zlib.decompressobj(-15)
    try:
        out = d.decompress(data, limit)
    except zlib.error as e:
        raise CodecError(f"dados compactados inválidos: {e}") from None
    if d.unconsumed_tail:
        raise CodecError("conteúdo grande demais depois de descompactado")
    return out


class Codec:
    def __init__(self, sync_key: str = None):
        self.encrypted = bool(sync_key)
        if self.encrypted:
            try:
                raw = base64.b64decode(sync_key, validate=True)
            except ValueError:
                raw = b""
            if len(raw) != 32:
                raise CodecError("SYNC_KEY precisa ter 32 bytes em base64 (gere com: remote_client.py synckey)")
            self._aead = AESGCM(_derive(raw, b"claude-memory-sync/enc"))
            self._mac = _derive(raw, b"claude-memory-sync/id")
        self.name = "aes-256-gcm" if self.encrypted else "plain"

    def blob_id(self, data: bytes) -> str:
        if self.encrypted:
            return hmac.new(self._mac, data, hashlib.sha256).hexdigest()
        return hashlib.sha256(data).hexdigest()

    def seal(self, data: bytes, aad: str) -> bytes:
        packed = _deflate(data)
        if not self.encrypted:
            return PLAIN + packed
        nonce = os.urandom(12)
        return ENCRYPTED + nonce + self._aead.encrypt(nonce, packed, aad.encode())

    def open(self, sealed: bytes, aad: str, limit: int = MAX_PLAIN) -> bytes:
        kind, body = bytes(sealed[:2]), sealed[2:]
        if kind == PLAIN:
            return _inflate(body, limit)
        if kind != ENCRYPTED:
            raise CodecError("formato de dado desconhecido (cliente desatualizado?)")
        if not self.encrypted:
            raise CodecError("os dados estão criptografados: defina a mesma SYNC_KEY dos outros PCs no .env")
        try:
            packed = self._aead.decrypt(body[:12], body[12:], aad.encode())
        except InvalidTag:
            raise CodecError("não consegui decifrar: a SYNC_KEY deste PC é diferente da dos outros") from None
        return _inflate(packed, limit)
