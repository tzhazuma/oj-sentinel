"""Authenticated HTTP session for a Hydro OJ instance.

The session is deliberately conservative: a small thread pool, exponential
backoff on 5xx/timeouts, and cookies shared between worker threads.  Student
credentials are never written to disk by this module.
"""
from __future__ import annotations

import random
import threading
import time
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urljoin

import requests

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36 OJ-Sentinel/0.1"
)


class HydroError(RuntimeError):
    pass


class HydroAuthError(HydroError):
    pass


class HydroClient:
    def __init__(
        self,
        base_url: str,
        domain: str,
        *,
        timeout: float = 25.0,
        min_interval: float = 0.03,
        user_agent: str = DEFAULT_UA,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.domain = domain.strip("/")
        self.timeout = timeout
        self.min_interval = min_interval
        self.user_agent = user_agent
        self._local = threading.local()
        self._cookies: dict[str, str] = {}
        self._lock = threading.Lock()
        self.username = ""
        self.uid = 0
        self._logged_in = False

    # ------------------------------------------------------------------ http
    def _session(self) -> requests.Session:
        sess = getattr(self._local, "session", None)
        if sess is None:
            sess = requests.Session()
            sess.headers.update(
                {
                    "User-Agent": self.user_agent,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                }
            )
            with self._lock:
                for k, v in self._cookies.items():
                    sess.cookies.set(k, v, domain=self.base_url.split("//", 1)[-1].split("/")[0])
            self._local.session = sess
        return sess

    def _remember_cookies(self, sess: requests.Session) -> None:
        with self._lock:
            for c in sess.cookies:
                self._cookies[c.name] = c.value

    @property
    def cookies(self) -> dict[str, str]:
        with self._lock:
            return dict(self._cookies)

    def set_cookies(self, cookies: dict[str, str]) -> None:
        with self._lock:
            self._cookies.update(cookies)

    def url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        if path.startswith("/"):
            return urljoin(self.base_url + "/", path.lstrip("/"))
        return urljoin(self.base_url + "/", path)

    def domain_url(self, path: str = "") -> str:
        return self.url(f"/d/{self.domain}/{path.lstrip('/')}")

    def request(self, method: str, url: str, *, retries: int = 3, **kwargs) -> requests.Response:
        if not url.startswith("http"):
            url = self.url(url)
        last: Exception | None = None
        for attempt in range(retries):
            if self.min_interval:
                time.sleep(self.min_interval + random.random() * self.min_interval)
            try:
                resp = self._session().request(method, url, timeout=self.timeout, **kwargs)
                self._remember_cookies(self._session())
                if resp.status_code in (429, 500, 502, 503, 504):
                    last = HydroError(f"HTTP {resp.status_code} for {url}")
                    time.sleep(1.5 * (attempt + 1))
                    continue
                return resp
            except (requests.RequestException, OSError) as exc:  # noqa: PERF203
                last = exc
                time.sleep(1.2 * (attempt + 1))
        raise HydroError(f"request failed after {retries} attempts: {url}: {last}")

    def get(self, path: str, **kwargs) -> requests.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> requests.Response:
        return self.request("POST", path, **kwargs)

    # ----------------------------------------------------------------- login
    def login(self, username: str, password: str) -> bool:
        self.get("/login")
        resp = self.request(
            "POST",
            "/login",
            data={
                "uname": username,
                "password": password,
                "rememberme": "on",
                "tfa": "",
                "authnChallenge": "",
                "login_submit": "Login",
            },
            allow_redirects=False,
        )
        ok = resp.status_code in (301, 302, 303) and "sid" in self.cookies
        if not ok:
            body = resp.text
            if "error" in body.lower() and "login" in body.lower():
                raise HydroAuthError("Hydro rejected the username or password")
            raise HydroAuthError(f"login failed (HTTP {resp.status_code})")
        self.username = username
        self._logged_in = True
        self.verify_login()
        return True

    def restore_session(self, cookies: dict[str, str], username: str = "") -> None:
        self.set_cookies(cookies)
        self.username = username
        self._logged_in = True

    def verify_login(self) -> bool:
        resp = self.get(self.domain_url("homework"))
        page = resp.text
        if "Login" in page and self.username and self.username not in page:
            raise HydroAuthError("session is not authenticated for this domain")
        if 'name="uname"' in page and "logout" not in page.lower():
            raise HydroAuthError("session is not authenticated")
        return True

    # -------------------------------------------------------------- discovery
    def list_homeworks(self):
        from .parser import parse_homework_list

        resp = self.get(self.domain_url("homework"))
        hws = parse_homework_list(resp.text)
        if not hws and "Login" in resp.text:
            raise HydroAuthError("not logged in")
        return hws

    def get_homework(self, homework_id: str):
        from .parser import parse_homework_detail

        resp = self.get(self.domain_url(f"homework/{homework_id}"))
        hw = parse_homework_detail(resp.text, homework_id)
        try:
            for item in self.list_homeworks():
                if item.id == homework_id:
                    hw.title = item.title
                    break
        except Exception:  # noqa: BLE001
            pass
        if hw.title in {"Homework Introduction", "作业介绍", "Problem", "题目"}:
            hw.title = ""
        return hw

    def get_scoreboard(self, homework_id: str):
        from .parser import parse_scoreboard

        resp = self.get(self.domain_url(f"homework/{homework_id}/scoreboard"))
        return parse_scoreboard(resp.text, homework_id)

    def get_problem(self, key: str, homework_id: str | None = None, fetch_files: bool = True):
        from .parser import parse_problem, parse_files

        path = f"p/{key}"
        if homework_id:
            path += f"?tid={homework_id}"
        resp = self.get(self.domain_url(path))
        page = resp.text
        files_page = None
        if fetch_files:
            fresp = self.get(self.domain_url(f"p/{key}/files"))
            if fresp.status_code == 200 and "error - Hydro" not in fresp.text:
                files_page = fresp.text
        return parse_problem(page, key, files_page)

    def list_records(self, homework_id: str, *, max_pages: int | None = None,
                     on_page: Callable[[int, int], None] | None = None):
        from .parser import parse_record_list

        page = 1
        while True:
            resp = self.get(self.domain_url(f"record?tid={homework_id}&page={page}"))
            rows = parse_record_list(resp.text)
            if on_page:
                on_page(page, len(rows))
            if not rows:
                break
            yield from rows
            if max_pages and page >= max_pages:
                break
            page += 1
            if page > 500:
                break

    def get_record(self, rid: str):
        from .parser import parse_record_detail

        resp = self.get(self.domain_url(f"record/{rid}"))
        if "<title>error - Hydro</title>" in resp.text:
            raise HydroError(f"record {rid}: Hydro returned an error page")
        return parse_record_detail(resp.text, rid)

    def download(self, url: str, dest: Path) -> int:
        resp = self.request("GET", url, retries=4, stream=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        total = 0
        with tmp.open("wb") as fh:
            for chunk in resp.iter_content(64 * 1024):
                if chunk:
                    fh.write(chunk)
                    total += len(chunk)
        tmp.replace(dest)
        return total

    # ------------------------------------------------------------- concurrency
    def fetch_many(self, items: Iterable, worker: Callable, *, workers: int = 6,
                   label: str = "", progress: Callable[[int, int, str], None] | None = None):
        """Run ``worker(item)`` over ``items`` with a bounded thread pool.

        Yields ``(item, result, error)`` as they complete; callers decide how to
        persist results.  Keeping the pool in the client means every worker
        reuses the same cookie jar.
        """
        import queue
        from concurrent.futures import ThreadPoolExecutor, as_completed

        items = list(items)
        results = []
        total = len(items)
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(worker, item): item for item in items}
            for fut in as_completed(futures):
                item = futures[fut]
                try:
                    value = fut.result()
                    err = None
                except Exception as exc:  # noqa: BLE001
                    value, err = None, exc
                done += 1
                if progress:
                    progress(done, total, label)
                results.append((item, value, err))
        return results
