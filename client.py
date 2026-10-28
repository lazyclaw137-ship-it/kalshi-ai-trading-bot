"""Signed Kalshi REST client. GETs retry with bounded backoff; POST/DELETE never auto-retry."""
import base64, os, random, time
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

RETRY_STATUS = {429, 500, 502, 503, 504}
RETRY_AFTER_CAP = 30.0
RETRY_BUDGET = 25.0
PREFIX = "/trade-api/v2"


class KalshiError(Exception):
    def __init__(self, status, text):
        super().__init__(f"HTTP {status}: {text}")
        self.status = status


def is_network_failure(exc):
    """True for failures that mean 'the network/server is unhealthy' (not a 404 or a bad request)."""
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    return isinstance(exc, KalshiError) and exc.status in RETRY_STATUS


def load_key(path):
    with open(os.path.expanduser(path), "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def sign(key, ts, method, path):
    msg = (ts + method.upper() + path.split("?")[0]).encode()
    if isinstance(key, rsa.RSAPrivateKey):
        sig = key.sign(msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                        salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
    else:
        sig = key.sign(msg)
    return base64.b64encode(sig).decode()


def backoff_delay(attempt, retry_after=None):
    if retry_after is not None:
        try:
            return min(float(retry_after), RETRY_AFTER_CAP)
        except ValueError:
            pass
    return min(8.0, 0.5 * 2 ** attempt) + random.random() * 0.2


class Client:
    def __init__(self, base_url, key_id=None, key_path=None, max_retries=4, timeout=(5, 10),
                 session=None, sleep=time.sleep, clock=time.monotonic, budget=RETRY_BUDGET):
        self.base = base_url.rstrip("/")
        self.key_id = key_id
        self.key = load_key(key_path) if key_path else None
        self.max_retries, self.timeout = max_retries, timeout
        self.s = session or requests.Session()
        self.sleep, self.clock, self.budget = sleep, clock, budget

    def _headers(self, method, path):
        if not self.key:
            return {}
        ts = str(int(time.time() * 1000))
        return {"KALSHI-ACCESS-KEY": self.key_id, "KALSHI-ACCESS-TIMESTAMP": ts,
                "KALSHI-ACCESS-SIGNATURE": sign(self.key, ts, method, PREFIX + path)}

    def request(self, method, path, params=None, json=None):
        can_retry = method.upper() == "GET"
        start, attempt = self.clock(), 0
        while True:
            try:
                r = self.s.request(method, self.base + path, params=params, json=json,
                                   headers=self._headers(method, path), timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout):
                delay = backoff_delay(attempt)
                if can_retry and attempt < self.max_retries and self.clock() - start + delay <= self.budget:
                    self.sleep(delay); attempt += 1; continue
                raise
            if r.status_code in RETRY_STATUS and can_retry and attempt < self.max_retries:
                delay = backoff_delay(attempt, r.headers.get("Retry-After"))
                if self.clock() - start + delay <= self.budget:
                    self.sleep(delay); attempt += 1; continue
            if r.status_code >= 400:
                raise KalshiError(r.status_code, r.text[:300])
            return r.json() if r.content else {}

    def get(self, path, params=None):
        return self.request("GET", path, params=params)
