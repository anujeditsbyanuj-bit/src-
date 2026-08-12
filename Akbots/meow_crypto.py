# Akbots - Don't Remove Credit - @AkBots_Official
#
# Shared crypto helpers for the Meow* providers (Akbots/meowtv_provider.py,
# Akbots/meowverse_provider.py, Akbots/meowtoon_provider.py) — ported from
# the meowtv project's src/lib/crypto.ts and the inline crypto helpers in
# src/lib/providers/meowverse.ts.
#
# Requires pycryptodome (added to requirements.txt).

import base64
import gzip
import hashlib

from Crypto.Cipher import AES, DES3


# ── Castle / MeowTV (crypto.ts) ─────────────────────────────────────────

def castle_derive_key(api_key_b64: str, suffix: str = "") -> bytes:
    """Mirrors crypto.ts's deriveKey(): base64-decoded security key + an
    ASCII suffix, padded/truncated to exactly 16 bytes."""
    api_key_bytes = base64.b64decode(api_key_b64)
    suffix_bytes = suffix.encode("ascii", errors="ignore")
    key_material = api_key_bytes + suffix_bytes

    if len(key_material) < 16:
        return key_material + b"\x00" * (16 - len(key_material))
    return key_material[:16]


def castle_decrypt(encrypted_b64: str, api_key_b64: str, suffix: str = "") -> str | None:
    """Mirrors crypto.ts's decryptData(): AES-128-CBC where the derived key
    is reused as the IV, PKCS7-padded. Returns None on any failure (bad
    key, corrupt payload, etc.) same as the TS original."""
    try:
        aes_key = castle_derive_key(api_key_b64, suffix)
        iv = aes_key
        encrypted = base64.b64decode(encrypted_b64)

        cipher = AES.new(aes_key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypted)

        # Strip PKCS7 padding
        pad_len = decrypted[-1]
        if 1 <= pad_len <= 16:
            decrypted = decrypted[:-pad_len]

        return decrypted.decode("utf-8", errors="replace")
    except Exception:
        return None


# ── MeowVerse (inline helpers in meowverse.ts) ──────────────────────────

def des3_decrypt(encrypted_b64: str, key: str, iv: str) -> str:
    """Mirrors meowverse.ts's des3Decrypt(): DES-EDE3-CBC, key truncated to
    24 bytes, PKCS7-unpadded. Returns '' on failure (matches TS)."""
    try:
        key_bytes = key.encode("ascii", errors="ignore")[:24]
        iv_bytes = iv.encode("ascii", errors="ignore")
        cipher = DES3.new(key_bytes, DES3.MODE_CBC, iv_bytes)
        decrypted = cipher.decrypt(base64.b64decode(encrypted_b64))

        pad_len = decrypted[-1]
        if 1 <= pad_len <= 8:
            decrypted = decrypted[:-pad_len]

        return decrypted.decode("utf-8", errors="replace")
    except Exception:
        return ""


def aes_decrypt_gzip(encrypted_b64: str, key: str, iv: str) -> str:
    """Mirrors meowverse.ts's aesDecrypt(): AES-128-CBC with NO padding
    removal (raw block output, same as Node's setAutoPadding(false)), then
    gzip-decompress, then trim trailing junk back to the last valid JSON
    closing brace/bracket (handles leftover CBC padding bytes)."""
    try:
        key_bytes = key.encode("ascii", errors="ignore")
        iv_bytes = iv.encode("ascii", errors="ignore")
        data = base64.b64decode(encrypted_b64)

        cipher = AES.new(key_bytes, AES.MODE_CBC, iv_bytes)
        decrypted = cipher.decrypt(data)

        try:
            result_text = gzip.decompress(decrypted).decode("utf-8", errors="replace")
        except Exception:
            result_text = decrypted.decode("utf-8", errors="replace")

        last_brace = result_text.rfind("}")
        last_bracket = result_text.rfind("]")
        cut_at = max(last_brace, last_bracket)
        if cut_at != -1:
            return result_text[: cut_at + 1]
        return result_text.strip()
    except Exception:
        return ""


def md5_hex(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def generate_sign(secret: str, cur_time: str, device_id: str) -> str:
    """Mirrors meowverse.ts's generateSign()."""
    return md5_hex((secret or "") + device_id + cur_time).upper()


def generate_p2p_token(vod_id: str, timestamp: str, device_id: str, salt: str) -> str:
    """Mirrors meowverse.ts's generateP2PToken()."""
    return md5_hex(salt + device_id + vod_id + timestamp).upper()
