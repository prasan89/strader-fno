from __future__ import annotations

import hmac
import html
import secrets
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from .brokers import Upstox
from .http import ApiError, request_json
from .settings import load_settings, write_settings, validate_settings
from .telegram import Telegram


class SetupContext:
    def __init__(self, path: Path, origin: str, transport=request_json):
        self.path, self.origin, self.transport = path, origin, transport
        self.csrf = secrets.token_urlsafe(32)
        self.state = None
        self.pending = None
        self.complete = False

    def validate_csrf(self, origin: str | None, token: str) -> None:
        if origin != self.origin or not hmac.compare_digest(self.csrf, token):
            raise ValueError("Invalid setup request; use the local setup page")

    def save_verified(self, settings: dict[str, str]) -> None:
        validate_settings(settings)
        Upstox(settings.get("UPSTOX_ACCESS_TOKEN", ""), self.transport).verify()
        telegram = Telegram(settings.get("TELEGRAM_BOT_TOKEN", ""), settings.get("TELEGRAM_CHAT_ID", ""), self.transport)
        telegram.verify()
        try:
            telegram.send("Strader FNO: Telegram connection verified. This is a setup test.")
        except ApiError as exc:
            if exc.status == 403:
                raise ValueError("Telegram rejected delivery. Start your alert bot, then enter your own numeric Telegram user ID as Chat ID, not another bot's ID.") from None
            raise
        write_settings(self.path, settings)
        self.complete = True

    def configure(self, fields: dict[str, str]) -> str | None:
        settings = load_settings(self.path)
        for key in ("UPSTOX_ACCESS_TOKEN", "UPSTOX_CLIENT_ID", "UPSTOX_CLIENT_SECRET",
                    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
            if fields.get(key, "").strip():
                settings[key] = fields[key].strip()
        validate_settings(settings)
        if fields.get("auth_method") == "oauth":
            settings["UPSTOX_CLIENT_ID"] = settings.get("UPSTOX_CLIENT_ID") or settings.get("UPSTOX_API_KEY", "")
            settings["UPSTOX_CLIENT_SECRET"] = settings.get("UPSTOX_CLIENT_SECRET") or settings.get("UPSTOX_API_SECRET", "")
            if not settings["UPSTOX_CLIENT_ID"] or not settings["UPSTOX_CLIENT_SECRET"]:
                raise ValueError("Enter the Upstox developer app's client ID and client secret")
            Telegram(settings.get("TELEGRAM_BOT_TOKEN", ""), settings.get("TELEGRAM_CHAT_ID", ""), self.transport).verify()
            self.state = secrets.token_urlsafe(32)
            settings["UPSTOX_REDIRECT_URI"] = self.origin + "/callback"
            self.pending = settings
            return "https://api.upstox.com/v2/login/authorization/dialog?" + urlencode({
                "response_type": "code", "client_id": settings["UPSTOX_CLIENT_ID"],
                "redirect_uri": settings["UPSTOX_REDIRECT_URI"], "state": self.state})
        self.save_verified(settings)
        return None

    def callback(self, state: str, code: str) -> None:
        if not self.state or not self.pending or not hmac.compare_digest(self.state, state) or not code:
            raise ValueError("Authorization state mismatch; restart the local setup flow")
        settings = self.pending.copy()
        self.state, self.pending = None, None  # Consume state once, before token exchange.
        result = self.transport("https://api.upstox.com/v2/login/authorization/token", service="Upstox OAuth",
                                form={"code": code, "client_id": settings["UPSTOX_CLIENT_ID"],
                                      "client_secret": settings["UPSTOX_CLIENT_SECRET"],
                                      "redirect_uri": settings["UPSTOX_REDIRECT_URI"], "grant_type": "authorization_code"})
        if not isinstance(result.get("access_token"), str) or not result["access_token"]:
            raise ApiError("Upstox OAuth")
        settings["UPSTOX_ACCESS_TOKEN"] = result["access_token"]
        self.save_verified(settings)


def page(context: SetupContext, message: str = "") -> str:
    return """<!doctype html><html><meta charset="utf-8"><title>Strader setup</title>
<style>body{font:16px system-ui;background:#f3f5f9;color:#17233a;margin:40px auto;max-width:680px;padding:24px}
form{background:white;padding:28px;border-radius:16px}input,select{display:block;width:100%;box-sizing:border-box;padding:11px;margin:7px 0 18px;border:1px solid #ccd4e0;border-radius:6px}button{background:#244be2;color:white;padding:13px 20px;border:0;border-radius:7px}small{color:#53627b}label{font-weight:600}.message{padding:15px;background:#e4ecff;border-radius:8px}</style>
<h1>Connect your scanner</h1><p>200 NSE F&amp;O stocks · Every five minutes · Telegram breakout and breakdown alerts</p>
MESSAGE
<form method="post" action="/configure"><input type="hidden" name="csrf" value="CSRF">
<h2>Upstox</h2><label>Connection method</label><select name="auth_method"><option value="token">I have an API access token</option><option value="oauth">Sign in using my developer app</option></select>
<label>Access token — one-year Analytics token recommended</label><input type="password" name="UPSTOX_ACCESS_TOKEN" autocomplete="off">
<p><small>To avoid daily token copying, generate a one-year token in Upstox Developer Apps → Analytics → Generate Token, then paste it above. Standard developer-app login still requires your approval when renewing.</small></p>
<small>For developer-app login, enter the two fields below instead. Register this redirect URL in your Upstox app: ORIGIN/callback</small>
<p><a href="https://account.upstox.com/developer/apps" target="_blank" rel="noopener noreferrer">Open Upstox developer apps</a></p>
<label>Client ID / API key</label><input name="UPSTOX_CLIENT_ID" autocomplete="off">
<label>Client secret / API secret</label><input type="password" name="UPSTOX_CLIENT_SECRET" autocomplete="off">
<h2>Telegram</h2><label>Bot token</label><input type="password" name="TELEGRAM_BOT_TOKEN" autocomplete="off">
<label>Chat ID — your numeric Telegram user ID</label><input name="TELEGRAM_CHAT_ID" autocomplete="off">
<p><small>Start the bot in Telegram first. Existing saved credentials can be reused by leaving their fields blank. Credentials stay in a local file with access restricted to your user account.</small></p>
<button>Save and send Telegram test</button></form></html>""".replace("MESSAGE", '<p class="message">' + html.escape(message) + '</p>' if message else "").replace("CSRF", context.csrf).replace("ORIGIN", html.escape(context.origin))


def run_setup(path: Path = Path(".env"), port: int = 8765) -> None:
    origin = f"http://127.0.0.1:{port}"
    context = SetupContext(path, origin)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # OAuth codes and tokens must never appear in access logs.

        def respond(self, status: int, body: str):
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self' https://api.upstox.com")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self):
            url = urlsplit(self.path)
            if self.headers.get("Host") != f"127.0.0.1:{port}":
                self.respond(403, "Invalid host")
                return
            if url.path == "/":
                self.respond(200, page(context))
            elif url.path == "/callback":
                fields = parse_qs(url.query)
                try:
                    context.callback(fields.get("state", [""])[0], fields.get("code", [""])[0])
                    self.respond(200, page(context, "Upstox and Telegram verified. Local setup is complete. You can close this page."))
                except (ValueError, ApiError) as exc:
                    self.respond(400, page(context, str(exc)))
            else:
                self.respond(404, "Not found")

        def do_POST(self):
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if self.path != "/configure" or not 0 < size <= 32768:
                    raise ValueError("Invalid setup form")
                fields = {key: values[0] for key, values in parse_qs(self.rfile.read(size).decode(), keep_blank_values=True).items()}
                context.validate_csrf(self.headers.get("Origin"), fields.get("csrf", ""))
                redirect = context.configure(fields)
                if redirect:
                    self.send_response(303)
                    self.send_header("Location", redirect)
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                else:
                    self.respond(200, page(context, "Upstox and Telegram verified. Local setup is complete. You can close this page."))
            except (ValueError, ApiError) as exc:
                self.respond(400, page(context, str(exc)))
    with HTTPServer(("127.0.0.1", port), Handler) as server:
        server.timeout = 1
        print("Open " + origin + " to configure Upstox and Telegram locally.", flush=True)
        while not context.complete:
            server.handle_request()
        print("Connections verified; local configuration saved.", flush=True)
