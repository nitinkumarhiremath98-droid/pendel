"""A small PostgreSQL client written with the Python standard library only.

PENDEL has no third-party dependencies, so instead of psycopg it speaks the
PostgreSQL wire protocol (v3) directly. It supports what PENDEL needs:

* TLS (sslmode=disable | prefer | require | verify-full), with SNI so it works
  with Neon, Supabase, Render Postgres, Railway, Aiven ...
* SCRAM-SHA-256, MD5 and cleartext password authentication
* the simple query protocol with client-side parameter quoting for ``?`` and
  ``:name`` placeholders (the same style sqlite3 uses)
* int / float / bool / numeric / text result conversion

It is deliberately not a general-purpose driver.
"""
import base64, functools, hashlib, hmac, math, os, re, socket, ssl, struct, urllib.parse


class Error(Exception):
    pass


class OperationalError(Error):
    """The connection failed or was lost. Safe to reconnect and retry outside a transaction."""


class DatabaseError(Error):
    def __init__(self, fields):
        self.fields = fields
        self.code = fields.get("C", "")
        self.detail = fields.get("D")
        super().__init__(f"{fields.get('M', 'database error')} ({self.code})" + (f": {self.detail}" if self.detail else ""))


class IntegrityError(DatabaseError):
    pass


def clean_url(url):
    """Accept what people actually paste: the string wrapped in quotes, a "psql '...'" snippet
    or a "DATABASE_URL=..." line copied from a dashboard."""
    s = (url or "").strip()
    m = re.search(r"postgres(?:ql)?://[^\s'\"]+", s)
    return m.group(0) if m else s


def parse_url(url):
    url = clean_url(url)
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("postgres", "postgresql"):
        start = url[:12] + "..." if url else "(empty)"
        raise ValueError(f"DATABASE_URL must start with postgresql:// but it starts with: {start}  "
                         "Copy the connection string from Neon (Connect button) and paste it between the quotes.")
    qs = dict(urllib.parse.parse_qsl(u.query))
    host = u.hostname or "localhost"
    local = host in ("localhost", "127.0.0.1", "::1")
    return {"host": host, "port": u.port or 5432,
            "user": urllib.parse.unquote(u.username or "postgres"),
            "password": urllib.parse.unquote(u.password or ""),
            "dbname": urllib.parse.unquote((u.path or "/").lstrip("/") or "postgres"),
            "sslmode": qs.get("sslmode", "prefer" if local else "require"),
            "options": qs.get("options")}


# ------------------------------------------------------------------ result conversion

_INT = {20, 21, 23, 26}
_FLOAT = {700, 701}


def _convert(oid, raw):
    if raw is None:
        return None
    s = raw.decode("utf-8")
    if oid in _INT:
        return int(s)
    if oid in _FLOAT:
        return float(s)
    if oid == 1700:  # numeric: SUM(int) and friends
        return int(s) if re.fullmatch(r"-?\d+", s) else float(s)
    if oid == 16:
        return s == "t"
    return s


# ------------------------------------------------------------------ parameters

def literal(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return f"({v})" if v < 0 else str(v)
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return f"'{v}'::float8"
        return f"({v!r})" if v < 0 else repr(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return "'\\x" + bytes(v).hex() + "'::bytea"
    s = str(v).replace("\x00", "")
    return "'" + s.replace("'", "''") + "'"


@functools.lru_cache(maxsize=4096)
def _split(sql):
    """Split SQL at placeholders that are outside quotes/comments.
    Returns (fragments, names): names[i] is None for '?' or the :name."""
    frags, names, buf, i, n = [], [], [], 0, len(sql)
    while i < n:
        c = sql[i]
        if c in "'\"":
            j = i + 1
            while j < n:
                if sql[j] == c:
                    if j + 1 < n and sql[j + 1] == c:
                        j += 2
                        continue
                    break
                j += 1
            buf.append(sql[i:j + 1]); i = j + 1
        elif c == "-" and sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j < 0 else j
            buf.append(sql[i:j]); i = j
        elif c == "/" and sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            j = n if j < 0 else j + 2
            buf.append(sql[i:j]); i = j
        elif c == "?":
            frags.append("".join(buf)); buf = []; names.append(None); i += 1
        elif c == ":" and i + 1 < n and (sql[i + 1].isalpha() or sql[i + 1] == "_") and (i == 0 or sql[i - 1] != ":"):
            m = re.match(r"[A-Za-z_]\w*", sql[i + 1:])
            frags.append("".join(buf)); buf = []; names.append(m.group(0)); i += 1 + m.end()
        else:
            buf.append(c); i += 1
    frags.append("".join(buf))
    return tuple(frags), tuple(names)


def bind(sql, params=()):
    frags, names = _split(sql)
    if not names:
        if params:
            raise Error("parameters given for a statement without placeholders")
        return sql
    out = [frags[0]]
    if isinstance(params, dict):
        for name, frag in zip(names, frags[1:]):
            if name is None:
                raise Error("mixing ? and :name placeholders")
            out.append(literal(params[name])); out.append(frag)
    else:
        params = list(params)
        if len(params) != len(names) or any(names):
            raise Error(f"statement expects {len(names)} parameters, got {len(params)}")
        for p, frag in zip(params, frags[1:]):
            out.append(literal(p)); out.append(frag)
    return "".join(out)


# ------------------------------------------------------------------ connection

class Cursor:
    def __init__(self, columns, rows, rowcount):
        self.description = [(c,) for c in columns] if columns else None
        self.columns, self.rows, self.rowcount = columns, rows, rowcount
        self._i = 0

    def fetchall(self):
        r = self.rows[self._i:]; self._i = len(self.rows); return r

    def fetchone(self):
        if self._i >= len(self.rows):
            return None
        self._i += 1
        return self.rows[self._i - 1]

    def __iter__(self):
        return iter(self.fetchall())


class Connection:
    def __init__(self, url, timeout=20, application_name="pendel"):
        self.cfg = parse_url(url)
        self.tx_status = "I"
        self.params = {}
        self.closed = False
        self._connect(timeout, application_name)

    # -- low-level io
    def _send(self, kind, payload=b""):
        try:
            self.sock.sendall((kind or b"") + struct.pack("!I", len(payload) + 4) + payload)
        except OSError as e:
            self._fail(e)

    def _recv_exact(self, n):
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self.sock.recv(n - len(buf))
            except OSError as e:
                self._fail(e)
            if not chunk:
                self._fail(ConnectionError("server closed the connection"))
            buf += chunk
        return bytes(buf)

    def _read(self):
        head = self._recv_exact(5)
        kind, length = head[:1], struct.unpack("!I", head[1:])[0]
        return kind, self._recv_exact(length - 4)

    def _fail(self, e):
        self.close()
        raise OperationalError(f"PostgreSQL connection lost: {e}") from e

    # -- startup
    def _connect(self, timeout, app):
        c = self.cfg
        try:
            sock = socket.create_connection((c["host"], c["port"]), timeout=timeout)
        except OSError as e:
            raise OperationalError(f"can't reach PostgreSQL at {c['host']}:{c['port']}: {e}") from e
        self.sock = sock
        if c["sslmode"] != "disable":
            sock.sendall(struct.pack("!II", 8, 80877103))
            answer = sock.recv(1)
            if answer == b"S":
                if c["sslmode"] in ("verify-full", "verify-ca"):
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = c["sslmode"] == "verify-full"
                else:  # libpq 'require': encrypted, certificate not verified
                    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                self.sock = ctx.wrap_socket(sock, server_hostname=c["host"])
            elif c["sslmode"] in ("require", "verify-full", "verify-ca"):
                sock.close()
                raise OperationalError("the PostgreSQL server does not support SSL but sslmode requires it")
        kv = {"user": c["user"], "database": c["dbname"], "application_name": app, "client_encoding": "UTF8"}
        if c["options"]:
            kv["options"] = c["options"]
        payload = struct.pack("!I", 196608) + b"".join(k.encode() + b"\0" + v.encode() + b"\0" for k, v in kv.items()) + b"\0"
        self._send(None, payload)
        self._authenticate()
        self.sock.settimeout(120)

    def _authenticate(self):
        scram = None
        while True:
            kind, body = self._read()
            if kind == b"R":
                code = struct.unpack("!I", body[:4])[0]
                if code == 0:
                    continue
                if code == 3:
                    self._send(b"p", self.cfg["password"].encode() + b"\0")
                elif code == 5:
                    salt = body[4:8]
                    inner = hashlib.md5((self.cfg["password"] + self.cfg["user"]).encode()).hexdigest().encode()
                    self._send(b"p", b"md5" + hashlib.md5(inner + salt).hexdigest().encode() + b"\0")
                elif code == 10:
                    mechs = [m.decode() for m in body[4:].split(b"\0") if m]
                    if "SCRAM-SHA-256" not in mechs:
                        raise OperationalError(f"unsupported authentication mechanisms: {mechs}")
                    scram = _Scram(self.cfg["password"])
                    first = scram.client_first().encode()
                    self._send(b"p", b"SCRAM-SHA-256\0" + struct.pack("!I", len(first)) + first)
                elif code == 11:
                    self._send(b"p", scram.client_final(body[4:].decode()).encode())
                elif code == 12:
                    scram.verify(body[4:].decode())
                else:
                    raise OperationalError(f"unsupported authentication method {code}")
            elif kind == b"E":
                self.close()
                raise OperationalError(_error_fields(body).get("M", "authentication failed"))
            elif kind == b"S":
                k, v, _ = body.split(b"\0", 2)
                self.params[k.decode()] = v.decode()
            elif kind == b"Z":
                self.tx_status = body.decode()
                return

    # -- queries
    def execute(self, sql, params=()):
        if self.closed:
            raise OperationalError("connection is closed")
        text = bind(sql, params)
        self._send(b"Q", text.encode("utf-8") + b"\0")
        columns, oids, rows, rowcount, error = None, None, [], -1, None
        while True:
            kind, body = self._read()
            if kind == b"T":
                n = struct.unpack("!H", body[:2])[0]
                pos, columns, oids = 2, [], []
                for _ in range(n):
                    end = body.index(b"\0", pos)
                    columns.append(body[pos:end].decode())
                    oids.append(struct.unpack("!I", body[end + 7:end + 11])[0])
                    pos = end + 19
                rows = []
            elif kind == b"D":
                n = struct.unpack("!H", body[:2])[0]
                pos, row = 2, []
                for i in range(n):
                    ln = struct.unpack("!i", body[pos:pos + 4])[0]; pos += 4
                    if ln < 0:
                        row.append(None)
                    else:
                        row.append(_convert(oids[i], body[pos:pos + ln])); pos += ln
                rows.append(tuple(row))
            elif kind == b"C":
                tag = body.rstrip(b"\0").decode().split()
                rowcount = int(tag[-1]) if tag and tag[-1].isdigit() else -1
            elif kind == b"E":
                f = _error_fields(body)
                error = (IntegrityError if f.get("C", "").startswith("23") else DatabaseError)(f)
            elif kind == b"Z":
                self.tx_status = body.decode()
                break
            # 'I' empty query, 'N' notice, 'S' parameter status, 'A' notification: ignored
        if error:
            raise error
        return Cursor(columns, rows, rowcount)

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.sock.sendall(b"X\0\0\0\4")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def _error_fields(body):
    out = {}
    for part in body.split(b"\0"):
        if part:
            out[chr(part[0])] = part[1:].decode("utf-8", "replace")
    return out


class _Scram:
    def __init__(self, password):
        self.password = password.encode()
        self.nonce = base64.b64encode(os.urandom(18)).decode()

    def client_first(self):
        self.first_bare = f"n=,r={self.nonce}"
        return "n,," + self.first_bare

    def client_final(self, server_first):
        a = dict(x.split("=", 1) for x in server_first.split(","))
        if not a["r"].startswith(self.nonce):
            raise OperationalError("SCRAM: server nonce mismatch")
        salted = hashlib.pbkdf2_hmac("sha256", self.password, base64.b64decode(a["s"]), int(a["i"]))
        client_key = hmac.new(salted, b"Client Key", "sha256").digest()
        stored = hashlib.sha256(client_key).digest()
        without_proof = f"c=biws,r={a['r']}"
        self.auth_message = f"{self.first_bare},{server_first},{without_proof}".encode()
        sig = hmac.new(stored, self.auth_message, "sha256").digest()
        proof = bytes(x ^ y for x, y in zip(client_key, sig))
        self.server_key = hmac.new(salted, b"Server Key", "sha256").digest()
        return f"{without_proof},p={base64.b64encode(proof).decode()}"

    def verify(self, server_final):
        a = dict(x.split("=", 1) for x in server_final.split(","))
        expected = base64.b64encode(hmac.new(self.server_key, self.auth_message, "sha256").digest()).decode()
        if not hmac.compare_digest(a.get("v", ""), expected):
            raise OperationalError("SCRAM: server signature mismatch")
