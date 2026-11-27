import json
import os
import time
import uuid

from flask import Flask, abort, redirect, render_template, request, send_from_directory, url_for, Response

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5MB

SUBJECTS = ["数学", "英語", "国語", "理科", "社会", "情報", "その他"]
ALLOWED = {"png", "jpg", "jpeg", "gif", "webp"}
CONN = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")  # set in App Service settings, never in code
TABLE = "posts"
CONTAINER = "images"

LOCAL_DIR = os.path.join(os.path.dirname(__file__), "local_data")
LOCAL_JSON = os.path.join(LOCAL_DIR, "posts.json")


# ---------- storage layer (Azure if configured, local files otherwise) ----------
def _table():
    from azure.data.tables import TableServiceClient
    t = TableServiceClient.from_connection_string(CONN).create_table_if_not_exists(TABLE)
    return t


def _container():
    from azure.storage.blob import BlobServiceClient
    svc = BlobServiceClient.from_connection_string(CONN)
    c = svc.get_container_client(CONTAINER)
    if not c.exists():
        c.create_container()
    return c


def _load_local():
    if not os.path.exists(LOCAL_JSON):
        return []
    with open(LOCAL_JSON, encoding="utf-8") as f:
        return json.load(f)


def _save_local(posts):
    os.makedirs(LOCAL_DIR, exist_ok=True)
    with open(LOCAL_JSON, "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False)


def list_posts(subject=None):
    if CONN:
        rows = [dict(e) for e in _table().list_entities()]
    else:
        rows = _load_local()
    if subject:
        rows = [r for r in rows if r.get("subject") == subject]
    rows.sort(key=lambda r: r.get("created", 0), reverse=True)
    return rows


def add_post(post):
    if CONN:
        _table().create_entity({"PartitionKey": "post", "RowKey": post["id"], **post})
    else:
        posts = _load_local()
        posts.append(post)
        _save_local(posts)


def mark_solved(post_id):
    if CONN:
        _table().update_entity({"PartitionKey": "post", "RowKey": post_id, "solved": True}, mode="merge")
    else:
        posts = _load_local()
        for p in posts:
            if p["id"] == post_id:
                p["solved"] = True
        _save_local(posts)


def save_image(file):
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED:
        return ""
    name = f"{uuid.uuid4().hex}.{ext}"
    if CONN:
        _container().upload_blob(name, file.read(), overwrite=True)
    else:
        os.makedirs(os.path.join(LOCAL_DIR, "images"), exist_ok=True)
        file.save(os.path.join(LOCAL_DIR, "images", name))
    return name


# ---------- routes ----------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/board")
def board():
    subject = request.args.get("subject")
    if subject not in SUBJECTS:
        subject = None
    return render_template("board.html", posts=list_posts(subject), subjects=SUBJECTS, current=subject)


@app.route("/post", methods=["POST"])
def post():
    nickname = request.form.get("nickname", "").strip()[:20] or "ななし"
    subject = request.form.get("subject", "その他")
    text = request.form.get("text", "").strip()[:500]
    if subject not in SUBJECTS or not text:
        return redirect(url_for("board"))
    image = ""
    f = request.files.get("image")
    if f and f.filename:
        image = save_image(f)
    add_post({
        "id": uuid.uuid4().hex,
        "nickname": nickname,
        "subject": subject,
        "text": text,
        "image": image,
        "solved": False,
        "created": int(time.time()),
    })
    return redirect(url_for("board"))


@app.route("/solve/<post_id>", methods=["POST"])
def solve(post_id):
    mark_solved(post_id)
    return redirect(request.referrer or url_for("board"))


@app.route("/img/<name>")
def img(name):
    if not name.replace(".", "").isalnum():
        abort(404)
    if CONN:
        try:
            data = _container().download_blob(name).readall()
        except Exception:
            abort(404)
        ext = name.rsplit(".", 1)[-1]
        return Response(data, mimetype=f"image/{'jpeg' if ext == 'jpg' else ext}")
    return send_from_directory(os.path.join(LOCAL_DIR, "images"), name)


if __name__ == "__main__":
    app.run(debug=True)
