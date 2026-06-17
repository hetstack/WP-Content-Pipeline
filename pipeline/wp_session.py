"""WordPress Session with Anti-Bot Challenge Support (Fixed for MyBoard/ByetHost)."""
import re
import time
import logging
import requests
import urllib3
from urllib.parse import urlparse

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = logging.getLogger(__name__)

try:
    from Crypto.Cipher import AES
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False
    log.warning("pycryptodome not installed - AES challenge will not work")

class WordPressSession:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.verify = False
        
        parsed = urlparse(self.base_url)
        self.hostname = parsed.netloc
        
        self.session.headers.update({
            "Host": self.hostname,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "max-age=0",
        })
        
        self.username = None
        self.password = None
        self.challenge_cookie_name = None

    def set_credentials(self, username, password):
        self.username = username
        self.password = password

    def _solve_challenge(self):
        if not HAS_CRYPTO:
            return False

        try:
            log.info(f"Pobieranie strony challenge z {self.base_url}...")
            resp = self.session.get(self.base_url + "/", timeout=15)
            
            if resp.status_code == 200 and ("aes.js" not in resp.text and "toNumbers" not in resp.text):
                return True

            content = resp.text
            keys = re.findall(r'toNumbers\("([a-f0-9]+)"\)', content)
            if len(keys) < 3:
                return False
            
            key_hex, iv_hex, cipher_hex = keys[0], keys[1], keys[2]

            cookie_match = re.search(r'document\.cookie="([^"]+)=', content)
            cookie_name = cookie_match.group(1) if cookie_match else "DO-NOT-SHARE-THIS-LINK-WITH-ANYONE"
            
            key = bytes.fromhex(key_hex)
            iv = bytes.fromhex(iv_hex)
            ciphertext = bytes.fromhex(cipher_hex)

            cipher = AES.new(key, AES.MODE_CBC, iv)
            decrypted_bytes = cipher.decrypt(ciphertext)
            cookie_value = decrypted_bytes.hex().lower()
            
            self.session.cookies.set(cookie_name, cookie_value, domain=self.hostname, path='/')
            self.challenge_cookie_name = cookie_name

            time.sleep(1)
            check = self.session.get(self.base_url + "/", timeout=15)
            
            return "aes.js" not in check.text and "toNumbers" not in check.text

        except Exception as e:
            log.error(f"Błąd challenge: {e}")
            return False

    def request(self, method, endpoint, **kwargs):
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        
        if self.username and self.password:
            self.session.auth = (self.username, self.password)

        try:
            response = self.session.request(method, url, **kwargs)
            
            content_type = response.headers.get("Content-Type", "")
            if "aes.js" in response.text or ("text/html" in content_type and "wp-json" in url):
                if self._solve_challenge():
                    response = self.session.request(method, url, **kwargs)
            
            return response
            
        except Exception as e:
            log.error(f"Request failed: {e}")
            raise

    def get(self, endpoint, **kwargs):
        return self.request("GET", endpoint, **kwargs)

    def post(self, endpoint, **kwargs):
        return self.request("POST", endpoint, **kwargs)
    
    def test_connection(self):
        res = {"auth_ok": False, "challenge_solved": False, "api_accessible": False}
        try:
            res["challenge_solved"] = self._solve_challenge()
            r = self.get("wp-json/")
            if r.status_code == 200 and "namespace" in r.text:
                res["api_accessible"] = True
            if self.username:
                r2 = self.get("wp-json/wp/v2/users/me")
                if r2.status_code == 200 and "id" in r2.text:
                    res["auth_ok"] = True
                    res["user"] = r2.json().get("name")
        except Exception as e:
            res["error"] = str(e)
        return res
