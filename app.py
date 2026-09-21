from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import threading
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

import keyring
from keyring.errors import KeyringError
import requests
from flask import Flask, jsonify, make_response, render_template, request
from waitress import serve

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "ozon_assistant.db"
SERVICE = "OzonAssistant"
OZON_URL = "https://api-seller.ozon.ru"
VERSION = "0.5.0"
UPDATE_MANIFEST_URL = "https://raw.githubusercontent.com/tacticatrus-spec/ozon-pomoshchnik/main/update.json"
TT_OPTIMIZATION_PATH = APP_DIR / "tt_optimization.json"
TT_OPTIMIZATION_URL = "https://raw.githubusercontent.com/tacticatrus-spec/ozon-pomoshchnik/main/tt_optimization.json"

app = Flask(__name__)


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS products(
          product_id INTEGER PRIMARY KEY, offer_id TEXT, name TEXT, sku INTEGER,
          price REAL DEFAULT 0, old_price REAL DEFAULT 0, competitor_price REAL,
          stock INTEGER DEFAULT 0, cost REAL DEFAULT 0, commission_pct REAL DEFAULT 0,
          logistics REAL DEFAULT 0, updated_at TEXT,
          seller_price REAL DEFAULT 0, buyer_price REAL DEFAULT 0,
          ozon_min_price REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS proposals(
          id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL,
          current_price REAL NOT NULL, proposed_price REAL NOT NULL, reason TEXT,
          status TEXT DEFAULT 'pending', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS orders(
          posting_number TEXT PRIMARY KEY, scheme TEXT, status TEXT, created_at TEXT,
          amount REAL DEFAULT 0, payload TEXT
        );
        CREATE TABLE IF NOT EXISTS messages(
          id TEXT PRIMARY KEY, kind TEXT, product_id INTEGER, text TEXT, status TEXT,
          created_at TEXT, payload TEXT
        );
        CREATE TABLE IF NOT EXISTS events(
          id INTEGER PRIMARY KEY AUTOINCREMENT, level TEXT, message TEXT, created_at TEXT
        );
        """)
        # Existing installations keep their database; add new columns in place.
        columns = {row[1] for row in c.execute("PRAGMA table_info(products)")}
        for name in ("seller_price", "buyer_price", "ozon_min_price"):
            if name not in columns:
                c.execute(f"ALTER TABLE products ADD COLUMN {name} REAL DEFAULT 0")


def setting(key, default=""):
    with db() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def save_setting(key, value):
    with db() as c:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def number(value, default=0.0):
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return float(default)


def target_margin_pct():
    return min(90.0, max(0.0, number(setting("target_margin_pct", "15"), 15)))


def profit_metrics(product, margin_pct=None):
    """Local management estimate. No cost or margin data is sent to Ozon."""
    margin_pct = target_margin_pct() if margin_pct is None else number(margin_pct, 15)
    seller_price = number(product.get("seller_price")) or number(product.get("price"))
    buyer_price = number(product.get("buyer_price")) or seller_price
    cost = number(product.get("cost"))
    logistics = number(product.get("logistics"))
    commission_pct = min(99.0, max(0.0, number(product.get("commission_pct"))))
    commission = seller_price * commission_pct / 100
    profit = seller_price - cost - logistics - commission
    margin = profit / seller_price * 100 if seller_price > 0 else 0
    break_even_denominator = 1 - commission_pct / 100
    safe_denominator = 1 - (commission_pct + margin_pct) / 100
    break_even = (cost + logistics) / break_even_denominator if break_even_denominator > 0 else 0
    safe_price = (cost + logistics) / safe_denominator if safe_denominator > 0 else 0
    if cost <= 0:
        status = "missing_cost"
    elif profit < 0:
        status = "loss"
    elif margin < margin_pct:
        status = "below_margin"
    else:
        status = "safe"
    return {
        "seller_price": round(seller_price, 2),
        "buyer_price": round(buyer_price, 2),
        "commission": round(commission, 2),
        "profit": round(profit, 2),
        "margin_pct": round(margin, 2),
        "break_even_price": round(break_even, 2),
        "safe_price": round(safe_price, 2),
        "profit_status": status,
    }


def profit_report(products=None, margin_pct=None):
    if products is None:
        with db() as c:
            products = [dict(row) for row in c.execute("SELECT * FROM products")]
    summary = {"loss": 0, "below_margin": 0, "missing_cost": 0, "safe": 0}
    enriched = []
    for product in products:
        item = dict(product)
        item.update(profit_metrics(item, margin_pct))
        summary[item["profit_status"]] += 1
        enriched.append(item)
    return enriched, summary


def cost_rules():
    try:
        rules = json.loads(setting("cost_rules", "[]"))
    except (TypeError, ValueError):
        return []
    valid = []
    for rule in rules if isinstance(rules, list) else []:
        try:
            prefix = str(rule.get("prefix") or "").strip().upper()
            cost = float(rule.get("cost"))
        except (AttributeError, TypeError, ValueError):
            continue
        if prefix and cost >= 0:
            valid.append({"prefix": prefix, "cost": cost})
    return valid


def cost_for_offer(offer_id, rules=None):
    offer_id = str(offer_id or "").upper()
    for rule in sorted(rules if rules is not None else cost_rules(), key=lambda x: len(x["prefix"]), reverse=True):
        if offer_id.startswith(rule["prefix"]):
            return rule["cost"]
    return None


def apply_cost_rules(force=False):
    """Apply locally saved rules without sending cost data to Ozon."""
    rules = cost_rules()
    changed = 0
    with db() as c:
        rows = c.execute("SELECT product_id, offer_id, cost FROM products").fetchall()
        for row in rows:
            cost = cost_for_offer(row["offer_id"], rules)
            if cost is None or (not force and float(row["cost"] or 0) != 0):
                continue
            if float(row["cost"] or 0) != cost:
                c.execute("UPDATE products SET cost=? WHERE product_id=?", (cost, row["product_id"]))
                changed += 1
    return changed


def apply_one_cost_rule(prefix, cost, force=True):
    changed = 0
    with db() as c:
        rows = c.execute("SELECT product_id, offer_id, cost FROM products").fetchall()
        for row in rows:
            if not str(row["offer_id"] or "").upper().startswith(prefix):
                continue
            if not force and float(row["cost"] or 0) != 0:
                continue
            if float(row["cost"] or 0) != cost:
                c.execute("UPDATE products SET cost=? WHERE product_id=?", (cost, row["product_id"]))
                changed += 1
    return changed


def log(message, level="info"):
    with db() as c:
        c.execute("INSERT INTO events(level,message,created_at) VALUES(?,?,?)", (level, message, datetime.now().isoformat(timespec="seconds")))


def secret(name):
    try:
        return keyring.get_password(SERVICE, name) or ""
    except KeyringError:
        return os.environ.get(f"OZON_ASSISTANT_{name.upper()}", "")


class OzonError(RuntimeError):
    pass


class Ozon:
    def __init__(self):
        self.client_id = secret("client_id")
        self.api_key = secret("api_key")

    def call(self, path, body=None):
        if not self.client_id or not self.api_key:
            raise OzonError("Сначала сохраните Client ID и API Key")
        try:
            r = requests.post(OZON_URL + path, json=body or {}, headers={
                "Client-Id": self.client_id, "Api-Key": self.api_key,
                "Content-Type": "application/json"
            }, timeout=35)
        except requests.RequestException as e:
            raise OzonError(f"Ошибка соединения с Ozon: {e}") from e
        if not r.ok:
            try:
                detail = r.json()
            except ValueError:
                detail = r.text[:300]
            raise OzonError(f"Ozon API {r.status_code}: {detail}")
        return r.json()

    def product_ids(self):
        out, cursor = [], ""
        while True:
            data = self.call("/v3/product/list", {"filter": {"visibility": "ALL"}, "last_id": cursor, "limit": 1000})
            result = data.get("result", data)
            items = result.get("items", [])
            out.extend(items)
            cursor = result.get("last_id", "")
            if not items or not cursor or len(items) < 1000:
                return out

    def prices(self, ids):
        result = []
        for i in range(0, len(ids), 1000):
            body = {"filter": {"product_id": [str(x) for x in ids[i:i+1000]], "visibility": "ALL"}, "cursor": "", "limit": 1000}
            data = self.call("/v5/product/info/prices", body)
            result.extend(data.get("items", data.get("result", {}).get("items", [])))
        return result

    def product_info(self, ids):
        result = []
        for i in range(0, len(ids), 1000):
            body = {"product_id": [str(x) for x in ids[i:i+1000]]}
            data = self.call("/v3/product/info/list", body)
            result.extend(data.get("result", {}).get("items", data.get("items", [])))
        return result

    def product_attributes(self):
        """Return every product card with its ordinary and complex attributes."""
        result, last_id = [], ""
        while True:
            data = self.call("/v4/product/info/attributes", {
                "filter": {"visibility": "ALL"},
                "last_id": last_id,
                "limit": 1000,
                "sort_dir": "ASC",
            })
            items = data.get("result", data.get("items", []))
            if isinstance(items, dict):
                items = items.get("items", [])
            result.extend(items)
            next_id = data.get("last_id", "")
            if not items or not next_id or next_id == last_id or len(items) < 1000:
                return result
            last_id = next_id

    def update_attributes(self, items):
        """Partially update only the supplied attributes; other card fields stay intact."""
        task_ids = []
        for i in range(0, len(items), 100):
            data = self.call("/v1/product/attributes/update", {"items": items[i:i+100]})
            task_id = data.get("task_id") or data.get("result", {}).get("task_id")
            if task_id:
                task_ids.append(task_id)
        return task_ids

    def stocks(self):
        data = self.call("/v4/product/info/stocks", {"filter": {"visibility": "ALL"}, "limit": 1000, "cursor": ""})
        return data.get("items", data.get("result", {}).get("items", []))

    def competitor(self, product_id):
        data = self.call("/v1/pricing-strategy/product/info", {"product_id": int(product_id)})
        return data.get("result", {})

    def set_price(self, offer_id, price, old_price=0):
        item = {"offer_id": offer_id, "price": str(round(price, 2)), "currency_code": "RUB"}
        if old_price and old_price > price:
            item["old_price"] = str(round(old_price, 2))
        return self.call("/v1/product/import/prices", {"prices": [item]})

    def postings(self, scheme, since, to):
        if scheme == "FBS":
            return self.call("/v3/posting/fbs/list", {"dir":"ASC", "filter":{"since":since,"to":to}, "limit":1000, "offset":0, "with":{"analytics_data":True,"financial_data":True}}).get("result", {}).get("postings", [])
        return self.call("/v2/posting/fbo/list", {"dir":"ASC", "filter":{"since":since,"to":to}, "limit":1000, "offset":0, "with":{"analytics_data":True,"financial_data":True}}).get("result", [])

    def reviews(self):
        data = self.call("/v1/review/list", {"limit":100, "sort_dir":"DESC", "status":"UNPROCESSED"})
        return data.get("reviews", data.get("result", {}).get("reviews", []))

    def questions(self):
        data = self.call("/v1/question/list", {"filter":{"status":"UNPROCESSED"}, "last_id":"", "limit":100})
        return data.get("questions", data.get("result", {}).get("questions", []))

    def answer_review(self, review_id, text):
        return self.call("/v1/review/comment/create", {"review_id": review_id, "text": text, "mark_review_as_processed": True})

    def answer_question(self, question_id, text):
        return self.call("/v1/question/answer/create", {"question_id": question_id, "text": text})


def notify(text):
    token, chat_id = secret("telegram_token"), setting("telegram_chat_id")
    if not token or not chat_id:
        return
    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=15).raise_for_status()


def demo_data():
    now = datetime.now().isoformat(timespec="seconds")
    rows = [
        (101, "DEMO-001", "Демонстрационный товар", 900001, 1490, 1790, 1450, 24, 620, 18, 95, now),
        (102, "DEMO-002", "Второй товар", 900002, 2490, 2890, 2590, 7, 1100, 17, 130, now),
    ]
    with db() as c:
        c.executemany("""INSERT OR REPLACE INTO products(
            product_id,offer_id,name,sku,price,old_price,competitor_price,
            stock,cost,commission_pct,logistics,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    log("Загружены демонстрационные товары")


def sync_products():
    api = Ozon()
    listing = api.product_ids()
    base = {int(x.get("product_id", 0)): x for x in listing if x.get("product_id")}
    prices = api.prices(list(base)) if base else []
    try:
        details = {int(x.get("id") or x.get("product_id", 0)): x for x in api.product_info(list(base))}
    except OzonError as e:
        details = {}
        log(f"Названия товаров: {e}", "warning")
    stocks = api.stocks()
    stock_map = {}
    for x in stocks:
        pid = int(x.get("product_id", 0))
        stock_map[pid] = sum(int(s.get("present", 0)) for s in x.get("stocks", []))
    now = datetime.now().isoformat(timespec="seconds")
    with db() as c:
        c.execute("DELETE FROM products WHERE offer_id LIKE 'DEMO-%'")
        for x in prices:
            pid = int(x.get("product_id", 0))
            b = base.get(pid, {})
            d = details.get(pid, {})
            p = x.get("price", {})
            seller_price = number(p.get("marketing_seller_price"))
            if seller_price <= 0:
                seller_price = number(p.get("price") or x.get("price"))
            buyer_price = number(p.get("marketing_price"))
            if buyer_price <= 0:
                buyer_price = seller_price
            old = number(p.get("old_price") or x.get("old_price"))
            ozon_min = number(p.get("min_price") or x.get("min_price"))
            c.execute("""INSERT INTO products(
                product_id,offer_id,name,sku,price,old_price,stock,updated_at,
                seller_price,buyer_price,ozon_min_price
              ) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(product_id) DO UPDATE SET
              offer_id=excluded.offer_id,name=excluded.name,sku=excluded.sku,price=excluded.price,
              old_price=excluded.old_price,stock=excluded.stock,updated_at=excluded.updated_at,
              seller_price=excluded.seller_price,buyer_price=excluded.buyer_price,
              ozon_min_price=excluded.ozon_min_price""",
              (pid, x.get("offer_id") or d.get("offer_id") or b.get("offer_id", ""),
               d.get("name") or x.get("name") or b.get("name") or f"Товар {pid}",
               d.get("sku") or x.get("sku", 0), seller_price, old, stock_map.get(pid, 0), now,
               seller_price, buyer_price, ozon_min))
    log(f"Синхронизировано товаров: {len(prices)}")
    apply_cost_rules(force=False)
    notify(f"OZON Assistant: синхронизировано товаров — {len(prices)}")
    notify_profit_risks()
    return len(prices)


def notify_profit_risks():
    _, summary = profit_report()
    signature = json.dumps(summary, sort_keys=True)
    if signature == setting("last_profit_alert_signature"):
        return
    save_setting("last_profit_alert_signature", signature)
    risky = summary["loss"] + summary["below_margin"]
    if risky:
        notify(
            "OZON Assistant — Защитник прибыли: "
            f"в убытке {summary['loss']}, ниже целевой маржи {summary['below_margin']}. "
            "Откройте раздел «Товары и цены». Цены автоматически не менялись."
        )


def attribute_replacements(search_text, replacement_text):
    """Build a preview and a partial-update payload for exact attribute values."""
    needle = search_text.strip().casefold()
    replacement_text = replacement_text.strip()
    if not needle or not replacement_text:
        raise ValueError("Заполните текст для поиска и замены")

    preview, update_items, skipped_videos = [], [], []
    for product in Ozon().product_attributes():
        product_updates = []
        for group_name in ("attributes", "complex_attributes"):
            for attribute in product.get(group_name) or []:
                values = attribute.get("values") or []
                if not any(needle in str(v.get("value") or "").casefold() for v in values):
                    continue
                # Ozon exposes uploaded video file names as a service complex
                # attribute. They cannot be renamed by the partial attributes API.
                if int(attribute.get("id") or 0) == 21837 and int(attribute.get("complex_id") or 0) == 100001:
                    for value in values:
                        old_value = str(value.get("value") or "")
                        if needle not in old_value.casefold():
                            continue
                        skipped_videos.append({
                            "product_id": product.get("id"),
                            "offer_id": product.get("offer_id", ""),
                            "name": product.get("name") or f"Товар {product.get('id', '')}",
                            "attribute_id": int(attribute.get("id") or 0),
                            "complex_id": int(attribute.get("complex_id") or 0),
                            "file_name": old_value,
                        })
                    continue
                new_values = []
                for value in values:
                    old_value = str(value.get("value") or "")
                    changed = needle in old_value.casefold()
                    new_value = re.sub(re.escape(search_text.strip()), replacement_text, old_value,
                                       flags=re.IGNORECASE) if changed else old_value
                    new_values.append({
                        "dictionary_value_id": int(value.get("dictionary_value_id") or 0),
                        "value": new_value,
                    })
                    if changed:
                        preview.append({
                            "product_id": product.get("id"),
                            "offer_id": product.get("offer_id", ""),
                            "name": product.get("name") or f"Товар {product.get('id', '')}",
                            "attribute_id": attribute.get("id"),
                            "complex_id": int(attribute.get("complex_id") or 0),
                            "old_value": old_value,
                            "new_value": new_value,
                            "dictionary_value_id": int(value.get("dictionary_value_id") or 0),
                        })
                product_updates.append({
                    "id": int(attribute.get("id")),
                    "complex_id": int(attribute.get("complex_id") or 0),
                    "values": new_values,
                })
        if product_updates:
            update_items.append({"offer_id": product.get("offer_id", ""), "attributes": product_updates})
    return preview, update_items, skipped_videos


def sync_orders():
    api = Ozon()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)
    since, to = start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z")
    count = 0
    with db() as c:
        for scheme in ("FBS", "FBO"):
            for p in api.postings(scheme, since, to):
                number = p.get("posting_number") or p.get("order_number")
                if not number: continue
                amount = sum(float(x.get("price", 0) or 0) * int(x.get("quantity", 1) or 1) for x in p.get("products", []))
                c.execute("INSERT OR REPLACE INTO orders VALUES(?,?,?,?,?,?)", (number, scheme, p.get("status", ""), p.get("created_at") or p.get("in_process_at", ""), amount, json.dumps(p, ensure_ascii=False)))
                count += 1
    log(f"Синхронизировано заказов за 30 дней: {count}")
    return count


def sync_messages():
    api, count = Ozon(), 0
    methods = [("review", api.reviews), ("question", api.questions)]
    with db() as c:
        for kind, method in methods:
            try:
                items = method()
            except OzonError as e:
                log(f"{kind}: {e}", "warning")
                continue
            for x in items:
                mid = str(x.get("id") or x.get("review_id") or x.get("question_id"))
                txt = x.get("text") or x.get("question_text") or x.get("content", "")
                c.execute("INSERT OR REPLACE INTO messages VALUES(?,?,?,?,?,?,?)", (mid, kind, x.get("product_id"), txt, x.get("status", "UNPROCESSED"), x.get("published_at") or x.get("created_at", ""), json.dumps(x, ensure_ascii=False)))
                count += 1
    if count: notify(f"OZON Assistant: новых отзывов и вопросов — {count}")
    return count


@app.get("/")
def home(): return render_template("index.html", version=VERSION)


@app.get("/api/dashboard")
def dashboard():
    with db() as c:
        products = [dict(x) for x in c.execute("SELECT * FROM products ORDER BY name")]
        orders = [dict(x) for x in c.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 100")]
        messages = [dict(x) for x in c.execute("SELECT * FROM messages ORDER BY created_at DESC LIMIT 100")]
        events = [dict(x) for x in c.execute("SELECT * FROM events ORDER BY id DESC LIMIT 30")]
        proposals = [dict(x) for x in c.execute("SELECT * FROM proposals WHERE status='pending' ORDER BY id DESC")]
        order_count = c.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    margin = target_margin_pct()
    products, risk_summary = profit_report(products, margin)
    return jsonify(products=products, orders=orders, order_count=order_count, messages=messages, events=events, proposals=proposals,
                   cost_rules=cost_rules(),
                   target_margin_pct=margin, risk_summary=risk_summary,
                   version=VERSION,
                   configured=bool(secret("client_id") and secret("api_key")), telegram=bool(secret("telegram_token") and setting("telegram_chat_id")))


def version_tuple(value):
    try:
        return tuple(int(part) for part in str(value).split("."))
    except ValueError:
        return (0,)


@app.get("/api/update/check")
def update_check():
    try:
        response = requests.get(UPDATE_MANIFEST_URL, params={"t": int(datetime.now().timestamp())}, timeout=15)
        response.raise_for_status()
        info = response.json()
        latest = str(info.get("version", VERSION))
        return jsonify(ok=True, current=VERSION, latest=latest,
                       available=version_tuple(latest) > version_tuple(VERSION),
                       notes=info.get("notes", ""))
    except Exception as e:
        return jsonify(ok=False, current=VERSION, message=f"Не удалось проверить обновления: {e}"), 502


@app.post("/api/update/install")
def update_install():
    if os.name != "nt":
        return jsonify(ok=False, message="Автообновление доступно в Windows"), 400
    update_bat = APP_DIR / "update.bat"
    if not update_bat.exists():
        return jsonify(ok=False, message="Файл update.bat не найден"), 500
    try:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        subprocess.Popen(["cmd.exe", "/c", str(update_bat)], cwd=APP_DIR,
                         creationflags=flags, close_fds=True)
        threading.Timer(1.2, lambda: os._exit(0)).start()
        return jsonify(ok=True, message="Обновление началось. Программа перезапустится автоматически.")
    except Exception as e:
        return jsonify(ok=False, message=f"Не удалось запустить обновление: {e}"), 500


@app.post("/api/settings")
def settings_save():
    x = request.json or {}
    for name in ("client_id", "api_key", "telegram_token"):
        if name in x and x[name]:
            try:
                keyring.set_password(SERVICE, name, str(x[name]).strip())
            except KeyringError as e:
                return jsonify(ok=False, message=f"Хранилище паролей недоступно: {e}"), 500
    if "telegram_chat_id" in x: save_setting("telegram_chat_id", x["telegram_chat_id"].strip())
    return jsonify(ok=True)


@app.post("/api/profit-protection/settings")
def profit_protection_settings():
    try:
        margin = float((request.json or {}).get("target_margin_pct"))
    except (TypeError, ValueError):
        return jsonify(ok=False, message="Введите целевую маржу числом"), 400
    if not 0 <= margin <= 90:
        return jsonify(ok=False, message="Целевая маржа должна быть от 0 до 90%"), 400
    save_setting("target_margin_pct", margin)
    save_setting("last_profit_alert_signature", "")
    log(f"Целевая маржа изменена: {margin:g}%")
    return jsonify(ok=True, target_margin_pct=margin,
                   message=f"Целевая маржа {margin:g}% сохранена локально")


@app.post("/api/test")
def test_connection():
    try:
        items = Ozon().call("/v3/product/list", {"filter":{"visibility":"ALL"}, "last_id":"", "limit":1})
        return jsonify(ok=True, message="Подключение к Ozon работает")
    except Exception as e: return jsonify(ok=False, message=str(e)), 400


@app.get("/api/telegram/chat")
def telegram_chat():
    token = secret("telegram_token")
    if not token: return jsonify(ok=False, message="Сначала сохраните токен Telegram"), 400
    try:
        data = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15).json()
        updates = data.get("result", [])
        chat = updates[-1].get("message", {}).get("chat", {}) if updates else {}
        if not chat.get("id"): raise RuntimeError("Сначала отправьте боту любое сообщение")
        save_setting("telegram_chat_id", chat["id"])
        notify("OZON Assistant подключён ✅")
        return jsonify(ok=True, chat_id=chat["id"])
    except Exception as e: return jsonify(ok=False, message=str(e)), 400


@app.post("/api/sync")
def sync():
    try:
        p, o, m = sync_products(), sync_orders(), sync_messages()
        return jsonify(ok=True, message=f"Готово: {p} товаров, {o} заказов, {m} сообщений")
    except Exception as e:
        log(str(e), "error")
        return jsonify(ok=False, message=str(e)), 400


@app.post("/api/demo")
def demo(): demo_data(); return jsonify(ok=True)


@app.post("/api/attributes/find")
def attributes_find():
    x = request.json or {}
    try:
        matches, _, skipped_videos = attribute_replacements(x.get("search", ""), x.get("replacement", ""))
        return jsonify(ok=True, matches=matches, count=len(matches),
                       skipped_videos=len(skipped_videos), skipped_video_items=skipped_videos)
    except Exception as e:
        return jsonify(ok=False, message=str(e)), 400


def tt_optimization_data():
    if not TT_OPTIMIZATION_PATH.exists():
        try:
            response = requests.get(TT_OPTIMIZATION_URL, params={"t": int(datetime.now().timestamp())}, timeout=20)
            response.raise_for_status()
            package = response.json()
            if not isinstance(package.get("items"), list) or not package["items"]:
                raise RuntimeError("получен пустой список улучшений")
            TT_OPTIMIZATION_PATH.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception as e:
            raise RuntimeError(f"Не удалось скачать файл улучшений TT: {e}") from e
    return json.loads(TT_OPTIMIZATION_PATH.read_text(encoding="utf-8"))


@app.get("/api/optimization/tt/preview")
def tt_optimization_preview():
    try:
        package = tt_optimization_data()
        proposed = {item["offer_id"]: item for item in package.get("items", [])}
        current = {
            str(item.get("offer_id") or ""): item
            for item in Ozon().product_attributes()
            if str(item.get("offer_id") or "") in proposed
        }
        preview = []
        for offer_id, change in proposed.items():
            card = current.get(offer_id, {})
            preview.append({
                "offer_id": offer_id,
                "found": bool(card),
                "old_name": card.get("name", "Товар не найден в Ozon"),
                "new_name": change["new_name"],
                "description": change["description"],
                "keywords": change["keywords"],
            })
        return jsonify(ok=True, count=len(preview), found=sum(1 for x in preview if x["found"]), items=preview)
    except Exception as e:
        return jsonify(ok=False, message=str(e)), 400


@app.post("/api/optimization/tt/apply")
def tt_optimization_apply():
    x = request.json or {}
    if x.get("confirmed") is not True:
        return jsonify(ok=False, message="Нужно подтвердить отправку улучшений"), 400
    try:
        package = tt_optimization_data()
        current_offers = {
            str(item.get("offer_id") or "")
            for item in Ozon().product_attributes()
        }
        updates = []
        skipped = []
        for item in package.get("items", []):
            offer_id = item["offer_id"]
            if offer_id not in current_offers:
                skipped.append(offer_id)
                continue
            updates.append({
                "offer_id": offer_id,
                "attributes": [
                    {"id": 4180, "complex_id": 0, "values": [{"dictionary_value_id": 0, "value": item["new_name"]}]},
                    {"id": 4191, "complex_id": 0, "values": [{"dictionary_value_id": 0, "value": item["description"]}]},
                    {"id": 23171, "complex_id": 0, "values": [{"dictionary_value_id": 0, "value": item["keywords"]}]},
                ],
            })
        if not updates:
            return jsonify(ok=False, message="Карточки TT не найдены"), 404
        task_ids = Ozon().update_attributes(updates)
        log(f"Прокачка TT: отправлено товаров {len(updates)}; пропущено {len(skipped)}")
        notify(f"OZON Assistant: отправлены улучшения для {len(updates)} карточек TT")
        return jsonify(ok=True, updated=len(updates), skipped=skipped, task_ids=task_ids,
                       message=f"Улучшения отправлены в Ozon для {len(updates)} карточек")
    except Exception as e:
        log(f"Ошибка прокачки TT: {e}", "error")
        return jsonify(ok=False, message=str(e)), 400


@app.get("/api/products/export")
def products_export():
    """Download complete Seller API data for cards whose offer_id has a prefix."""
    prefix = str(request.args.get("prefix", "TT-")).strip()
    if not prefix:
        return jsonify(ok=False, message="Укажите начало артикула, например TT-"), 400
    try:
        api = Ozon()
        cards = [
            item for item in api.product_attributes()
            if str(item.get("offer_id") or "").casefold().startswith(prefix.casefold())
        ]
        product_ids = [int(item.get("id")) for item in cards if item.get("id")]
        info_by_id = {}
        info_error = ""
        if product_ids:
            try:
                info_by_id = {
                    int(item.get("id") or item.get("product_id")): item
                    for item in api.product_info(product_ids)
                    if item.get("id") or item.get("product_id")
                }
            except Exception as e:
                # Attributes are still valuable if the supplementary endpoint is
                # temporarily unavailable or has changed permissions.
                info_error = str(e)

        with db() as c:
            local_by_offer = {
                row["offer_id"]: dict(row)
                for row in c.execute(
                    "SELECT * FROM products WHERE lower(offer_id) LIKE lower(?) ORDER BY offer_id",
                    (prefix + "%",),
                )
            }

        items = []
        for card in sorted(cards, key=lambda x: str(x.get("offer_id") or "")):
            product_id = int(card.get("id") or 0)
            offer_id = str(card.get("offer_id") or "")
            items.append({
                "product_id": product_id,
                "offer_id": offer_id,
                "name": card.get("name") or local_by_offer.get(offer_id, {}).get("name", ""),
                "local": local_by_offer.get(offer_id, {}),
                "card": card,
                "product_info": info_by_id.get(product_id, {}),
            })

        payload = {
            "format": "ozon-assistant-card-export-v1",
            "app_version": VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "offer_id_prefix": prefix,
            "count": len(items),
            "supplementary_info_error": info_error,
            "items": items,
        }
        safe_prefix = re.sub(r"[^A-Za-z0-9_-]+", "_", prefix).strip("_") or "products"
        response = make_response(json.dumps(payload, ensure_ascii=False, indent=2))
        response.headers["Content-Type"] = "application/json; charset=utf-8"
        response.headers["Content-Disposition"] = f'attachment; filename="ozon_cards_{safe_prefix}.json"'
        log(f"Выгружены карточки с артикулами {prefix}: {len(items)}")
        return response
    except Exception as e:
        log(f"Ошибка выгрузки карточек {prefix}: {e}", "error")
        return jsonify(ok=False, message=str(e)), 400


@app.post("/api/attributes/replace")
def attributes_replace():
    x = request.json or {}
    if x.get("confirmed") is not True:
        return jsonify(ok=False, message="Нужно подтвердить замену"), 400
    search_text = str(x.get("search", "")).strip()
    replacement_text = str(x.get("replacement", "")).strip()
    try:
        matches, items, skipped_videos = attribute_replacements(search_text, replacement_text)
        if not matches:
            message = "Совпадений в изменяемых характеристиках нет"
            if skipped_videos:
                message += f"; пропущено названий видео: {len(skipped_videos)}"
            return jsonify(ok=True, count=0, task_ids=[], skipped_videos=len(skipped_videos),
                           skipped_video_items=skipped_videos, message=message)
        if any(not item.get("offer_id") for item in items):
            raise OzonError("У одного из товаров отсутствует артикул; замена остановлена")
        task_ids = Ozon().update_attributes(items)
        log(f"Характеристики: отправлена замена «{search_text}» → «{replacement_text}»; значений: {len(matches)}, товаров: {len(items)}")
        notify(f"OZON Assistant: отправлена замена «{search_text}» → «{replacement_text}» для {len(items)} товаров")
        message = f"Замена отправлена в Ozon: {len(matches)} значений в {len(items)} товарах"
        if skipped_videos:
            message += f"; названия видео пропущены: {len(skipped_videos)}"
        return jsonify(ok=True, count=len(matches), products=len(items), task_ids=task_ids,
                       skipped_videos=len(skipped_videos), skipped_video_items=skipped_videos,
                       message=message)
    except Exception as e:
        log(f"Ошибка замены характеристик: {e}", "error")
        return jsonify(ok=False, message=str(e)), 400


@app.post("/api/product/<int:pid>/cost")
def product_cost(pid):
    x = request.json or {}
    try:
        cost = float(x.get("cost", 0))
        commission_pct = float(x.get("commission_pct", 0))
        logistics = float(x.get("logistics", 0))
    except (TypeError, ValueError):
        return jsonify(ok=False, message="Себестоимость, комиссия и логистика должны быть числами"), 400
    if cost < 0 or logistics < 0 or not 0 <= commission_pct < 100:
        return jsonify(ok=False, message="Проверьте расходы: значения не могут быть отрицательными, комиссия — от 0 до 99%"), 400
    with db() as c:
        c.execute("UPDATE products SET cost=?,commission_pct=?,logistics=? WHERE product_id=?", (cost, commission_pct, logistics, pid))
    return jsonify(ok=True)


@app.post("/api/cost-rules/apply")
def cost_rule_apply():
    x = request.json or {}
    prefix = str(x.get("prefix") or "").strip().upper()
    try:
        cost = float(x.get("cost"))
    except (TypeError, ValueError):
        return jsonify(ok=False, message="Введите себестоимость числом"), 400
    if not prefix:
        return jsonify(ok=False, message="Введите начало артикула"), 400
    if cost < 0:
        return jsonify(ok=False, message="Себестоимость не может быть отрицательной"), 400
    rules = [rule for rule in cost_rules() if rule["prefix"] != prefix]
    rules.append({"prefix": prefix, "cost": cost})
    rules.sort(key=lambda rule: len(rule["prefix"]), reverse=True)
    save_setting("cost_rules", json.dumps(rules, ensure_ascii=False))
    changed = apply_one_cost_rule(prefix, cost, force=True)
    log(f"Локальное правило себестоимости {prefix}: обновлено товаров {changed}")
    return jsonify(ok=True, changed=changed, rules=rules,
                   message=f"Себестоимость сохранена локально. Обновлено товаров: {changed}")


@app.post("/api/product/<int:pid>/competitor")
def product_competitor(pid):
    try:
        x = Ozon().competitor(pid)
        price = float(x.get("strategy_product_price") or 0)
        with db() as c: c.execute("UPDATE products SET competitor_price=? WHERE product_id=?", (price, pid))
        return jsonify(ok=True, price=price, url=x.get("strategy_competitor_product_url"))
    except Exception as e: return jsonify(ok=False, message=str(e)), 400


@app.post("/api/product/<int:pid>/propose")
def propose(pid):
    x = request.json or {}
    with db() as c:
        p = c.execute("SELECT * FROM products WHERE product_id=?", (pid,)).fetchone()
        if not p: return jsonify(ok=False, message="Товар не найден"), 404
        margin = float(x.get("margin_pct", target_margin_pct()))
        min_price = profit_metrics(dict(p), margin)["safe_price"]
        target = (p["competitor_price"] - 1) if p["competitor_price"] else p["price"]
        proposed = round(max(min_price, target), 2)
        reason = f"Конкурент: {p['competitor_price'] or 'нет данных'}; минимум при марже {margin}%: {min_price:.2f}"
        c.execute("INSERT INTO proposals(product_id,current_price,proposed_price,reason,created_at) VALUES(?,?,?,?,?)", (pid,p["price"],proposed,reason,datetime.now().isoformat(timespec="seconds")))
    return jsonify(ok=True, price=proposed, reason=reason)


@app.post("/api/proposal/<int:proposal_id>/<action>")
def proposal_action(proposal_id, action):
    with db() as c:
        q = c.execute("SELECT q.*,p.offer_id,p.old_price FROM proposals q JOIN products p ON p.product_id=q.product_id WHERE q.id=?", (proposal_id,)).fetchone()
        if not q: return jsonify(ok=False, message="Предложение не найдено"), 404
        if action == "reject":
            c.execute("UPDATE proposals SET status='rejected' WHERE id=?", (proposal_id,))
            return jsonify(ok=True)
    if action != "approve": return jsonify(ok=False, message="Неизвестное действие"), 400
    try:
        Ozon().set_price(q["offer_id"], q["proposed_price"], q["old_price"])
        with db() as c:
            c.execute("UPDATE proposals SET status='approved' WHERE id=?", (proposal_id,))
            c.execute("UPDATE products SET price=?,seller_price=? WHERE product_id=?", (q["proposed_price"], q["proposed_price"], q["product_id"]))
        notify(f"Цена {q['offer_id']} изменена: {q['current_price']} → {q['proposed_price']} ₽")
        return jsonify(ok=True)
    except Exception as e: return jsonify(ok=False, message=str(e)), 400


@app.post("/api/message/<mid>/answer")
def answer(mid):
    text = (request.json or {}).get("text", "").strip()
    if not text: return jsonify(ok=False, message="Введите ответ"), 400
    with db() as c: m = c.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    if not m: return jsonify(ok=False, message="Сообщение не найдено"), 404
    try:
        api = Ozon()
        api.answer_review(mid, text) if m["kind"] == "review" else api.answer_question(mid, text)
        with db() as c: c.execute("UPDATE messages SET status='PROCESSED' WHERE id=?", (mid,))
        return jsonify(ok=True)
    except Exception as e: return jsonify(ok=False, message=str(e)), 400


if __name__ == "__main__":
    init_db()
    apply_cost_rules(force=False)
    serve(app, host="127.0.0.1", port=8765, threads=8)
