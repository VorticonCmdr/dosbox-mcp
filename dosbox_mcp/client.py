# This file is part of the dosbox-mcp Project.
# License: GPL-2.0-or-later. Contact: dosbox-mcp@trinity2k.net
#

import httpx


class DosboxError(RuntimeError):
    """A non-2xx response from the engine. Carries the response's own
    shape (status/code/retryable) so a caller can decide what to do
    without parsing the message text - the engine's error_handler
    (webserver.cpp) has emitted {error, error_code, retryable} since
    protocol 1.2; `code` and `retryable` fall back to safe defaults
    against an older engine that only sends {error}.

    `body` is the full parsed JSON body (empty dict if it wasn't JSON or
    wasn't an object) - most non-2xx responses only need `message`, but
    a few (memory write's 412 conflict) carry real payload data with no
    top-level `error` key at all, which `message`'s fallback to the raw
    response text would otherwise leave stringified and unparsed."""

    def __init__(self, status: int, code: str, message: str,
                retryable: bool = False, route: str | None = None,
                body: dict | None = None, retry_after: str | None = None):
        super().__init__(f"{status}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.route = route
        self.body = body if body is not None else {}
        # Raw Retry-After header value (seconds, as sent) - e.g. 429 from
        # script/load's rate limiter. None when the response didn't send one.
        self.retry_after = retry_after


class DosboxClient:
    """Thin authenticated HTTP wrapper. Every request carries the bearer
    token and the X-Client: mcp header that drives the OSD activity signal."""

    def __init__(self, base_url, token, transport=None):
        self._base = base_url.rstrip("/")
        self._client = httpx.Client(
            headers={
                "Authorization": f"Bearer {token}",
                "X-Client": "mcp",
            },
            transport=transport,
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def _handle(self, resp, method: str, path: str):
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = {}
            if not isinstance(body, dict):
                body = {}
            message = body.get("error", resp.text)
            code = body.get("error_code", "unknown")
            retryable = bool(body.get("retryable", False))
            raise DosboxError(resp.status_code, code, message, retryable,
                              route=f"{method} {path}", body=body,
                              retry_after=resp.headers.get("retry-after"))
        ctype = resp.headers.get("content-type", "")
        if ctype.startswith("application/json"):
            return resp.json()
        return resp.content

    def get(self, path, params=None, headers=None, timeout=None):
        kwargs = {"params": params, "headers": headers}
        if timeout is not None:
            kwargs["timeout"] = timeout
        return self._handle(self._client.get(self._base + path, **kwargs),
                            "GET", path)

    def post(self, path, json=None, timeout=None):
        kwargs = {"json": json}
        if timeout is not None:
            kwargs["timeout"] = timeout
        return self._handle(self._client.post(self._base + path, **kwargs),
                            "POST", path)

    def post_text(self, path, text, content_type="text/plain", params=None,
                 timeout=None):
        # Some endpoints (script/load) take a raw body, not JSON. httpx
        # sets Content-Type from the content= kwarg's type, so pin it.
        kwargs = {
            "content": text.encode("utf-8"),
            "headers": {"Content-Type": content_type},
            "params": params,
        }
        if timeout is not None:
            kwargs["timeout"] = timeout
        return self._handle(self._client.post(self._base + path, **kwargs),
                            "POST", path)

    def put(self, path, json=None, headers=None, timeout=None):
        kwargs = {"json": json, "headers": headers}
        if timeout is not None:
            kwargs["timeout"] = timeout
        return self._handle(self._client.put(self._base + path, **kwargs),
                            "PUT", path)

    def delete(self, path, json=None, timeout=None):
        kwargs = {"json": json}
        if timeout is not None:
            kwargs["timeout"] = timeout
        return self._handle(
            self._client.request("DELETE", self._base + path, **kwargs),
            "DELETE", path)
