# python_backend/acl.py
from __future__ import annotations

import os, sqlite3, json, base64, hashlib
from typing import Iterable, List, Dict, Optional, Tuple

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

# --- storage (sqlite) ---
DB_PATH = os.path.join(os.path.dirname(__file__), "acl.sqlite")

def _init_db() -> None:
    with sqlite3.connect(DB_PATH) as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS doc_wrapped_keys (
            cid TEXT NOT NULL,
            principal TEXT NOT NULL,
            wrapped_key BLOB NOT NULL,
            PRIMARY KEY (cid, principal)
        )
        """)
_init_db()

# --- master secret (key derivation root) ---
# Use ACL_MASTER_SECRET from env if present; else use/create a local file .acl_master_secret (gitignore it!)
MASTER_FILE = os.path.join(os.path.dirname(__file__), ".acl_master_secret")

def _load_master_secret() -> bytes:
    env = os.getenv("ACL_MASTER_SECRET")
    if env:
        try:
            # Accept raw base64 or raw bytes hex
            try:
                return base64.urlsafe_b64decode(env)
            except Exception:
                return bytes.fromhex(env)
        except Exception:
            pass
        # Fallback: raw utf-8
        return env.encode("utf-8")
    if os.path.exists(MASTER_FILE):
        return open(MASTER_FILE, "rb").read().strip()
    secret = os.urandom(32)
    with open(MASTER_FILE, "wb") as f:
        f.write(secret)
    return secret

_MASTER = _load_master_secret()

def _derive_kek_for_principal(principal: str) -> bytes:
    """
    Derive a deterministic 32-byte KEK for a principal string (e.g., "role:HOD", "student:S20841").
    HKDF(master, salt=principal, info=b"ACL-WRAP").
    """
    principal = principal.strip().encode("utf-8")
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=principal, info=b"ACL-WRAP")
    return hkdf.derive(_MASTER)

def _fernet_from_key(key32: bytes) -> Fernet:
    # Fernet expects base64-urlsafe key
    return Fernet(base64.urlsafe_b64encode(key32))

# --- principals helpers ---
DEFAULT_ROLE_PRINCIPALS = {"role:ADMIN", "role:HOD", "role:DEAN", "role:AR", "role:DVC"}

def default_principals(student_id: str) -> List[str]:
    sid = (student_id or "").strip().upper()
    out = list(DEFAULT_ROLE_PRINCIPALS)
    if sid:
        out.append(f"student:{sid}")
    return sorted(set(out))

# --- content hashing ---
def _canonical_bytes(doc: Dict) -> bytes:
    return json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8")

def _sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

# --- public API ---
def encrypt_and_store(doc: Dict, principals: Iterable[str], ipfs_client) -> Tuple[str, str]:
    """
    Envelope-encrypt the JSON doc with a random data key, wrap that data key once per principal,
    store ciphertext JSON to IPFS, and persist wrapped keys in sqlite.

    Returns (cid, content_hash_hex_of_plaintext).
    """
    principals = [p.strip() for p in principals if p and p.strip()]
    if not principals:
        raise ValueError("encrypt_and_store: principals list is empty")

    # 1) compute content hash over plaintext for on-chain integrity
    plain = _canonical_bytes(doc)
    content_hash = _sha256_hex(plain)

    # 2) generate a random data key and encrypt the plaintext
    data_key = os.urandom(32)
    f_plain = _fernet_from_key(data_key)
    ct_bytes = f_plain.encrypt(plain)  # includes its own random IV + timestamp
    ct_b64 = base64.urlsafe_b64encode(ct_bytes).decode("ascii")

    # 3) build encrypted payload (no plaintext fields leaked)
    kid = _sha256_hex(data_key)[:16]
    payload = {
        "enc": "fernet-v1",
        "alg": "Fernet",
        "kid": f"dk:{kid}",
        "ciphertext": ct_b64,
        # optional minimal meta for UX (safe to include):
        # "meta": {"student_id": doc.get("student_id"), "timestamp": doc.get("timestamp")}
    }

    # 4) store ciphertext payload on IPFS using existing client
    #    (works because your client already stores arbitrary dicts as JSON)
    res = ipfs_client.store_academic_document(payload)
    # your existing app code normalizes store result, but here we extract cid directly
    if isinstance(res, dict):
        cid = res.get("ipfs_hash") or res.get("cid") or res.get("Hash") or res.get("path") or ""
    elif isinstance(res, (list, tuple)) and len(res) >= 1:
        cid = str(res[0])
    else:
        cid = str(res)
    if not cid:
        raise RuntimeError("IPFS store returned no CID")

    # 5) wrap data key for each principal & persist
    with sqlite3.connect(DB_PATH) as c:
        for p in principals:
            kek = _derive_kek_for_principal(p)
            f_wrap = _fernet_from_key(kek)
            wrapped = f_wrap.encrypt(data_key)
            c.execute("""
                INSERT OR REPLACE INTO doc_wrapped_keys (cid, principal, wrapped_key)
                VALUES (?, ?, ?)
            """, (cid, p, wrapped))

    return cid, content_hash

def _find_wrapped_for(cid: str, principals: Iterable[str]) -> Optional[Tuple[str, bytes]]:
    plist = [p.strip() for p in principals if p and p.strip()]
    if not plist:
        return None
    q_marks = ",".join("?" * len(plist))
    with sqlite3.connect(DB_PATH) as c:
        row = c.execute(f"""
            SELECT principal, wrapped_key FROM doc_wrapped_keys
            WHERE cid=? AND principal IN ({q_marks})
            ORDER BY principal LIMIT 1
        """, (cid, *plist)).fetchone()
        if not row:
            return None
        return (row[0], row[1])

def decrypt_for_user(user: Optional[Dict], student_id: str, cid: str, enc_payload: Dict) -> Dict:
    """
    Given logged-in user, the target student_id, the CID and the encrypted payload JSON,
    return the decrypted plaintext JSON dict if user is on the ACL. Raises on failure.
    """
    if not isinstance(enc_payload, dict) or enc_payload.get("enc") != "fernet-v1":
        raise ValueError("decrypt_for_user: payload is not an encrypted fernet-v1 document")

    # candidate principals for this user
    principals: List[str] = []
    if user:
        role = (user.get("role") or "").strip().upper()
        if role:
            principals.append(f"role:{role}")
        if role == "STUDENT":
            sid = (user.get("student_id") or "").strip().upper()
            if sid:
                principals.append(f"student:{sid}")

    # also allow exact student_id principal as a fallback (in case user record omits it)
    if student_id:
        principals.append(f"student:{(student_id or '').strip().upper()}")

    # find a wrapped key we can unwrap
    found = _find_wrapped_for(cid, principals)
    if not found:
        raise PermissionError("No wrapped key for your principals on this document")

    principal_used, wrapped = found
    kek = _derive_kek_for_principal(principal_used)
    f_wrap = _fernet_from_key(kek)
    data_key = f_wrap.decrypt(wrapped)

    # decrypt ciphertext
    ct_b64 = enc_payload.get("ciphertext") or ""
    if not ct_b64:
        raise ValueError("Encrypted payload missing ciphertext")
    ct_bytes = base64.urlsafe_b64decode(ct_b64.encode("ascii"))
    f_plain = _fernet_from_key(data_key)
    plain = f_plain.decrypt(ct_bytes)
    return json.loads(plain.decode("utf-8"))
