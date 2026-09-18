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
from flask import Flask, jsonify, render_template, request
from waitress import serve

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "ozon_assistant.db"
SERVICE = "OzonAssistant"
OZON_URL = "https://api-seller.ozon.ru"
VERSION = "0.4.2"
UPDATE_MANIFEST_URL = "https://raw.githubusercontent.com/tacticatrus-spec/ozon-pomoshchnik/main/update.json"

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
          logistics REAL DEFAULT 0, updated_at TEXT
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


def setting(key, default=""):
    with db() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def save_setting(key, value):
    with db() as c:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


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
            body = {
                "filter": {"product_id": [str(x) for x in ids[i:i+1000]], "visibility": "ALL"},
                "last_id": "",
                "limit": 1000,
            }
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
        c.executemany("INSERT OR REPLACE INTO products VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", rows)
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
            current = float(p.get("marketing_price") or p.get("price") or x.get("price", 0) or 0)
            old = float(p.get("old_price") or x.get("old_price", 0) or 0)
            c.execute("""INSERT INTO products(product_id,offer_id,name,sku,price,old_price,stock,updated_at)
              VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(product_id) DO UPDATE SET
              offer_id=excluded.offer_id,name=excluded.name,sku=excluded.sku,price=excluded.price,
              old_price=excluded.old_price,stock=excluded.stock,updated_at=excluded.updated_at""",
              (pid, x.get("offer_id") or d.get("offer_id") or b.get("offer_id", ""),
               d.get("name") or x.get("name") or b.get("name") or f"Товар {pid}",
               d.get("sku") or x.get("sku", 0), current, old, stock_map.get(pid, 0), now))
    log(f"Синхронизировано товаров: {len(prices)}")
    notify(f"Ozon Помощник: синхронизировано товаров — {len(prices)}")
    return len(prices)


def attribute_replacements(search_text, replacement_text):
    """Build a preview and a partial-update payload for exact attribute values."""
    needle = search_text.strip().casefold()
    replacement_text = replacement_text.strip()
    if not needle or not replacement_text:
        raise ValueError("Заполните текст для поиска и замены")

    preview, update_items, skipped_videos = [], [], 0
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
                    skipped_videos += sum(
                        1 for value in values
                        if needle in str(value.get("value") or "").casefold()
                    )
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
    if count: notify(f"Ozon Помощник: новых отзывов и вопросов — {count}")
    return count


@app.get("/")
def home(): return render_template("index.html")


@app.get("/api/dashboard")
def dashboard():
    with db() as c:
        products = [dict(x) for x in c.execute("SELECT * FROM products ORDER BY name")]
        orders = [dict(x) for x in c.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 100")]
        messages = [dict(x) for x in c.execute("SELECT * FROM messages ORDER BY created_at DESC LIMIT 100")]
        events = [dict(x) for x in c.execute("SELECT * FROM events ORDER BY id DESC LIMIT 30")]
        proposals = [dict(x) for x in c.execute("SELECT * FROM proposals WHERE status='pending' ORDER BY id DESC")]
        order_count = c.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    for p in products:
        fee = p["price"] * p["commission_pct"] / 100
        p["profit"] = round(p["price"] - p["cost"] - fee - p["logistics"], 2)
    return jsonify(products=products, orders=orders, order_count=order_count, messages=messages, events=events, proposals=proposals,
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
        notify("Ozon Помощник подключён ✅")
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
        return jsonify(ok=True, matches=matches, count=len(matches), skipped_videos=skipped_videos)
    except Exception as e:
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
                message += f"; пропущено названий видео: {skipped_videos}"
            return jsonify(ok=True, count=0, task_ids=[], skipped_videos=skipped_videos, message=message)
        if any(not item.get("offer_id") for item in items):
            raise OzonError("У одного из товаров отсутствует артикул; замена остановлена")
        task_ids = Ozon().update_attributes(items)
        log(f"Характеристики: отправлена замена «{search_text}» → «{replacement_text}»; значений: {len(matches)}, товаров: {len(items)}")
        notify(f"Ozon Помощник: отправлена замена «{search_text}» → «{replacement_text}» для {len(items)} товаров")
        message = f"Замена отправлена в Ozon: {len(matches)} значений в {len(items)} товарах"
        if skipped_videos:
            message += f"; названия видео пропущены: {skipped_videos}"
        return jsonify(ok=True, count=len(matches), products=len(items), task_ids=task_ids,
                       skipped_videos=skipped_videos, message=message)
    except Exception as e:
        log(f"Ошибка замены характеристик: {e}", "error")
        return jsonify(ok=False, message=str(e)), 400


@app.post("/api/product/<int:pid>/cost")
def product_cost(pid):
    x = request.json or {}
    with db() as c:
        c.execute("UPDATE products SET cost=?,commission_pct=?,logistics=? WHERE product_id=?", (float(x.get("cost",0)), float(x.get("commission_pct",0)), float(x.get("logistics",0)), pid))
    return jsonify(ok=True)


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
        floor = p["cost"] + p["logistics"]
        margin = float(x.get("margin_pct", 15))
        min_price = floor / max(0.01, 1 - (p["commission_pct"] + margin) / 100)
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
            c.execute("UPDATE products SET price=? WHERE product_id=?", (q["proposed_price"], q["product_id"]))
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
    serve(app, host="127.0.0.1", port=8765, threads=8)
