"""Science English web app — multi-account, session login, /admin backend."""
import sqlite3

from flask import (Flask, Response, g, jsonify, redirect, request,
                   send_from_directory, session)

from lib import corpus, directions, gloss as gloss_lib, ingest, schedule, srs
from lib.db import (connect, create_user, delete_user, get_secret,
                    get_setting, init_db, list_users, reset_password,
                    set_setting, today, user_row, word_row)
from lib.paths import INBOX, ROOT, STATIC

TIER_CN = {"D0": "领域核心", "D1": "学术高频", "D2": "文献语境"}

_POS = {"n": "n.", "v": "v.", "vt": "vt.", "vi": "vi.", "adj": "adj.", "adv": "adv.",
        "prep": "prep.", "conj": "conj.", "pron": "pron.", "num": "num.",
        "int": "int.", "art": "art.", "aux": "aux.", "abbr": "abbr.", "pl": "pl."}


def guess_pos(gloss: str) -> str:
    """ECDICT 中文释义常以 n./v./adj. 开头；抽出词性标签。"""
    if not gloss:
        return ""
    import re as _re
    toks = _re.findall(r"(?:^|[\s;；,，])(n|v|vt|vi|adj|adv|prep|conj|pron|num|int|art|aux|abbr|pl)\.",
                       gloss[:60].lower())
    seen = []
    for t in toks:
        if t not in seen:
            seen.append(t)
    return " ".join(_POS[t] for t in seen[:2])


def create_app() -> Flask:
    init_db()
    app = Flask(__name__, static_folder=str(STATIC), static_url_path="/static")
    with app.app_context():
        pass
    cxn = connect()
    app.config["SECRET_KEY"] = get_secret(cxn)
    cxn.close()

    @app.teardown_appcontext
    def _close(_e):
        g.pop("conn", None)

    def conn() -> sqlite3.Connection:
        if "conn" not in g:
            g.conn = connect()
        return g.conn

    # ---------------- 守卫 ----------------
    @app.before_request
    def guard():
        path = request.path
        if path.startswith("/static/") or path == "/login.html" or \
           path.startswith("/api/auth/") or path == "/favicon.ico":
            return None
        uid = session.get("uid")
        if not uid:
            if path.startswith("/api/"):
                return jsonify({"error": "未登录"}), 401
            return _redirect_html("login.html")
        u = user_row(conn(), uid=uid)
        if not u:
            session.clear()
            return jsonify({"error": "未登录"}), 401
        g.user = u
        g.acct = u["id"]
        if not u["is_admin"] and \
           (path == "/admin.html" or path.startswith("/api/admin/")):
            return Response("<h3>无权限访问后台</h3>", status=403)
        return None

    def _redirect_html(target: str) -> Response:
        return Response(
            f'<meta charset="utf-8"><script>location.replace("{target}")</script>',
            mimetype="text/html")

    # ---------------- 账号鉴权 ----------------
    @app.post("/api/auth/login")
    def api_login():
        data = request.get_json(force=True) or {}
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        from lib.db import check_login
        u = check_login(conn(), username, password)
        if not u:
            return jsonify({"error": "账号或密码错误"}), 401
        session["uid"] = u["id"]
        session.permanent = True
        return jsonify({"ok": True, "username": u["username"],
                        "is_admin": bool(u["is_admin"]),
                        "direction": u["direction"]})

    @app.post("/api/auth/logout")
    def api_logout():
        session.clear()
        return jsonify({"ok": True})

    @app.get("/api/auth/me")
    def api_me():
        if "uid" not in session:
            return jsonify({"ok": False}), 401
        u = user_row(conn(), uid=session["uid"])
        return jsonify({"ok": True, "username": u["username"],
                        "is_admin": bool(u["is_admin"]),
                        "direction": u["direction"]})

    # ---------------- 页面 ----------------
    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    for _page in ("index.html", "words.html", "sentence.html", "papers.html",
                  "progress.html", "settings.html", "login.html", "admin.html"):
        app.add_url_rule(
            f"/{_page}", f"page_{_page}",
            lambda _p=_page: send_from_directory(STATIC, _p))

    # Android 安装包：需登录（不能放公开 /static/，避免凭据外泄）
    @app.get("/api/app/apk")
    def api_app_apk():
        from flask import send_file
        apks = sorted((ROOT / "apps").glob("*.apk")) if (ROOT / "apps").exists() else []
        if not apks:
            return jsonify({"error": "no APK bundled"}), 404
        return send_file(apks[-1], as_attachment=True, download_name=apks[-1].name)

    def acct() -> int:
        return g.acct

    def seed_terms_for(conn) -> str:
        u = user_row(conn, uid=acct())
        row = conn.execute(
            "SELECT value FROM settings WHERE account_id=? AND key='seed_terms'",
            (acct(),)).fetchone()
        if row and row["value"]:
            return row["value"]
        return directions.default_terms(u["direction"] if u else "")

    # ---------- 学习 API ----------
    def card(row, cxn) -> dict:
        ctxs = cxn.execute(
            "SELECT c.id, c.sentence, c.section, p.title AS paper_title "
            "FROM contexts c LEFT JOIN papers p ON p.id=c.paper_id "
            "WHERE c.word_id=? ORDER BY c.id LIMIT 3", (row["id"],)).fetchall()
        return {
            "word_id": row["id"], "word": row["word"], "gloss": row["gloss_zh"],
            "phonetic": row["phonetic"] if "phonetic" in row.keys() else "",
            "pos": gloss_lib.pos_of(cxn, row["word"]) or guess_pos(row["gloss_zh"]),
            "tier": row["tier"], "tier_cn": TIER_CN.get(row["tier"], row["tier"]),
            "is_domain": bool(row["is_domain"]),
            "contexts": [dict(c) for c in ctxs],
        }

    @app.get("/api/summary")
    def api_summary():
        cxn = conn()
        a = acct()
        lg = schedule.log_today(cxn, a)
        daily_new = int(get_setting(cxn, a, "daily_new", "15"))
        daily_sent = int(get_setting(cxn, a, "daily_sent", "2"))
        cap = int(get_setting(cxn, a, "review_cap", "60"))
        due = srs.due_today_count(cxn, a)
        remaining_new = cxn.execute(
            "SELECT COUNT(*) c FROM words WHERE account_id=? AND status='todo' "
            "AND known=0", (a,)).fetchone()["c"]
        ph = schedule.phase(cxn, a)
        return jsonify({
            "date": today(), "phase": ph, "streak": schedule.streak(cxn, a),
            "recommend_new": schedule.recommend_new(cxn, a),
            "daily_new": daily_new, "daily_sent": daily_sent, "review_cap": cap,
            "due": due, "log": lg,
            "new_left": max(0, daily_new - lg["new_done"]),
            "sent_left": max(0, daily_sent - lg["sent_done"]),
            "review_left": max(0, cap - lg["review_done"]),
            "pool_remaining": remaining_new,
            "auto_speak": get_setting(cxn, a, "auto_speak", "1") == "1",
            "repeat_per_session": max(1, int(get_setting(cxn, a, "repeat_per_session", "3"))),
        })

    @app.get("/api/queue")
    def api_queue():
        cxn = conn()
        a = acct()
        sm = api_summary().get_json()
        new = srs.new_queue(cxn, a, sm["new_left"])
        due = srs.due_queue(cxn, a, sm["review_left"])
        return jsonify({"new": [card(r, cxn) for r in new],
                        "due": [card(r, cxn) for r in due],
                        "meta": {"new_left": sm["new_left"],
                                 "review_left": sm["review_left"]}})

    @app.post("/api/review")
    def api_review():
        data = request.get_json(force=True) or {}
        grade = int(data.get("grade", 2))
        if grade not in (0, 1, 2):
            return jsonify({"error": "grade 0/1/2"}), 400
        try:
            state = srs.apply(conn(), acct(), int(data["word_id"]), grade)
        except ValueError as e:
            return jsonify({"error": str(e)}), 403
        return jsonify(state)

    @app.post("/api/known")
    def api_known():
        cxn = conn()
        srs.mark_known(cxn, acct(),
                       int((request.get_json(force=True) or {}).get("word_id", 0)))
        return jsonify({"ok": True})

    @app.get("/api/search")
    def api_search():
        q = (request.args.get("q") or "").strip().lower()
        cxn = conn()
        if not q:
            return jsonify({})
        w = word_row(cxn, acct(), q)
        if not w:
            from lib.gloss import gloss as zh_gloss
            d = cxn.execute("SELECT phonetic FROM dict WHERE word=?",
                            (q,)).fetchone()
            ip = cxn.execute("SELECT ipa FROM ipa WHERE word=?",
                             (q,)).fetchone()
            if not d and not ip:
                return jsonify({"found": False})
            phon = (d["phonetic"] if d and d["phonetic"] else "") or (
                ip["ipa"] if ip else "")
            return jsonify({"found": True, "in_pool": False, "word": q,
                            "phonetic": phon,
                            "gloss": zh_gloss(cxn, q) or "<词典外，暂无释义>"})
        return jsonify({"found": True, "in_pool": True, **card(w, cxn)})

    # ---------- 句子翻译 ----------
    @app.get("/api/sentences/today")
    def api_sentences_today():
        cxn = conn()
        a = acct()
        limit = max(1, int(get_setting(cxn, a, "daily_sent", "2")))
        rows = cxn.execute(
            "SELECT c.id, c.sentence, c.word_id, w.word, w.gloss_zh, "
            "p.title AS paper_title FROM contexts c "
            "JOIN words w ON w.id=c.word_id "
            "JOIN papers p ON p.id=c.paper_id "
            "LEFT JOIN sent_notes n ON n.context_id=c.id "
            "WHERE w.account_id=? AND w.known=0 AND n.id IS NULL "
            "AND EXISTS(SELECT 1 FROM reviews r WHERE r.word_id=w.id) "
            "ORDER BY (SELECT r.reps FROM reviews r WHERE r.word_id=w.id) ASC, "
            "w.id DESC LIMIT ?", (a, limit * 3)).fetchall()
        out, seen_word = [], set()
        for r in rows:
            if len(out) >= limit:
                break
            if r["word_id"] in seen_word:
                continue
            seen_word.add(r["word_id"])
            out.append({"context_id": r["id"], "sentence": r["sentence"],
                        "word": r["word"], "gloss": r["gloss_zh"],
                        "paper_title": r["paper_title"]})
        return jsonify({"tasks": out})

    @app.post("/api/sentences/submit")
    def api_sentences_submit():
        data = request.get_json(force=True) or {}
        cxn = conn()
        cid, mytrans = int(data.get("context_id", 0)), data.get("my_trans", "")
        exists = cxn.execute(
            "SELECT id FROM sent_notes WHERE context_id=?", (cid,)).fetchone()
        if exists:
            cxn.execute("UPDATE sent_notes SET my_trans=? WHERE context_id=?",
                        (mytrans, cid))
        else:
            cxn.execute("INSERT INTO sent_notes(context_id,my_trans) VALUES(?,?)",
                        (cid, mytrans))
            cxn.execute(
                "INSERT INTO log(account_id,day,new_done,review_done,sent_done) "
                "VALUES(?,?,0,0,1) ON CONFLICT(account_id,day) DO UPDATE SET "
                "sent_done=sent_done+1", (acct(), today()))
        cxn.commit()
        return jsonify({"ok": True})

    @app.post("/api/sentences/translate")
    def api_sentences_translate():
        cxn = conn()
        provider = get_setting(cxn, acct(), "trans_provider")
        key = get_setting(cxn, acct(), "trans_key")
        if not provider or not key:
            return jsonify({"error": "未配置翻译接口（设置页填写后开启参考译文）"}), 400
        text = (request.get_json(force=True) or {}).get("text", "")
        if not text:
            return jsonify({"error": "empty"}), 400
        import requests
        if provider == "deepseek":
            r = requests.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": "deepseek-chat",
                      "messages": [
                          {"role": "system",
                           "content": "你是学术英语翻译。把用户英文句子译成通顺地道的中文，只输出译文。"},
                          {"role": "user", "content": text}],
                      "temperature": 0.2},
                timeout=60)
            r.raise_for_status()
            return jsonify({"translation":
                            r.json()["choices"][0]["message"]["content"].strip()})
        return jsonify({"error": f"未知翻译源 {provider}"}), 400

    # ---------- 文献库 ----------
    @app.get("/api/papers")
    def api_papers():
        cxn = conn()
        q = request.args.get("q") or ""
        sql = ("SELECT p.*, (SELECT COUNT(*) FROM contexts c WHERE c.paper_id=p.id) "
               "AS ctx_count FROM papers p WHERE p.account_id=? ")
        args = [acct()]
        if q:
            sql += "AND p.title LIKE ? "
            args.append(f"%{q}%")
        sql += "ORDER BY p.year DESC NULLS LAST, p.id DESC LIMIT 300"
        return jsonify({"papers": [dict(r) for r in cxn.execute(sql, args).fetchall()]})

    @app.post("/api/papers/import")
    def api_papers_import():
        cxn = conn()
        file = request.files.get("file")
        if not file:
            return jsonify({"error": "no file"}), 400
        dest = INBOX / (file.filename or "paper.pdf")
        dest.write_bytes(file.read())
        added = ingest.ingest_local_pdf(cxn, acct(), str(dest))
        return jsonify({"ok": True, "words_added": added})

    @app.post("/api/papers/fetch")
    def api_papers_fetch():
        cxn = conn()
        data = request.get_json(force=True) or {}
        source = data.get("source")
        termstr = data.get("terms") or seed_terms_for(cxn)
        terms = [t.strip() for t in termstr.replace(",", "|").split("|") if t.strip()]
        if not terms and source:
            terms = [""]
        limit = int(data.get("limit") or 200)
        added = corpus.fetch_into_db(cxn, terms, account_id=acct(),
                                     target_new=limit, source=source)
        words = ingest.extract_pending(cxn, acct(), limit=0, verbose=False)
        where = f"来源 {source}" if source else "主题"
        return jsonify({"ok": True, "added_papers": added,
                        "words_added": words, "where": where})

    @app.post("/api/papers/delete")
    def api_papers_delete():
        data = request.get_json(force=True) or {}
        ok = ingest.delete_paper(conn(), acct(), int(data.get("id", 0)))
        return jsonify({"ok": ok})

    @app.post("/api/papers/reading")
    def api_papers_reading():
        cxn = conn()
        data = request.get_json(force=True) or {}
        cxn.execute(
            "UPDATE papers SET is_reading=? WHERE id=? AND account_id=?",
            (1 if data.get("is_reading") else 0, int(data["id"]), acct()))
        cxn.commit()
        return jsonify({"ok": True})

    # ---------- 词表 ----------
    @app.get("/api/words")
    def api_words():
        cxn = conn()
        a = acct()
        q = (request.args.get("q") or "").strip()
        tier = request.args.get("tier") or ""
        st = request.args.get("status") or ""
        sort = request.args.get("sort") or "freq"
        page = max(1, int(request.args.get("page") or 1))
        per = min(300, int(request.args.get("per") or 60))
        where, args = ["w.account_id=?"], [a]
        if q:
            where.append("w.word LIKE ?")
            args.append(f"%{q}%")
        if tier in ("D0", "D1", "D2"):
            where.append("w.tier=?")
            args.append(tier)
        if st == "todo":
            where.append("w.status='todo' AND w.known=0")
        elif st == "learning":
            where.append("w.status='learning' AND w.known=0")
        elif st == "mastered":
            where.append("(w.status='mastered' OR w.known=1)")
        elif st == "known":
            where.append("w.known=1")
        wh = "WHERE " + " AND ".join(where)
        total = cxn.execute(f"SELECT COUNT(*) c FROM words w {wh}", args).fetchone()["c"]
        order = {"az": "w.word COLLATE NOCASE ASC", "new": "w.id DESC",
                 "freq": "w.is_domain DESC, w.freq DESC"}.get(sort,
                                                              "w.is_domain DESC, w.freq DESC")
        rows = cxn.execute(
            f"SELECT w.id,w.word,w.tier,w.gloss_zh,w.phonetic,w.is_domain,w.known,"
            f"w.status,w.freq,w.n_papers FROM words w {wh} ORDER BY {order} "
            f"LIMIT ? OFFSET ?", args + [per, (page - 1) * per]).fetchall()
        return jsonify({"words": [dict(r) for r in rows],
                        "total": total, "page": page, "per": per})

    @app.post("/api/words/known")
    def api_words_known():
        cxn = conn()
        data = request.get_json(force=True) or {}
        wid = int(data.get("word_id", 0))
        known = bool(data.get("known"))
        if known:
            cxn.execute(
                "UPDATE words SET known=1, status='mastered' "
                "WHERE id=? AND account_id=?", (wid, acct()))
        else:
            has_review = cxn.execute(
                "SELECT 1 FROM reviews r JOIN words w ON w.id=r.word_id "
                "WHERE w.id=? AND w.account_id=?", (wid, acct())).fetchone()
            cxn.execute(
                "UPDATE words SET known=0, status=? WHERE id=? AND account_id=?",
                ("learning" if has_review else "todo", wid, acct()))
        cxn.commit()
        return jsonify({"ok": True})

    # ---------- 进度 ----------
    @app.get("/api/progress")
    def api_progress():
        cxn = conn()
        a = acct()
        def one(q, p=()):
            return cxn.execute(q, p).fetchone()[0]
        lg = schedule.log_today(cxn, a)
        total = one("SELECT COUNT(*) FROM words WHERE account_id=?", (a,))
        by_status = {
            "todo": one("SELECT COUNT(*) FROM words WHERE account_id=? AND status='todo' AND known=0", (a,)),
            "learning": one("SELECT COUNT(*) FROM words WHERE account_id=? AND status='learning'", (a,)),
            "mastered": one("SELECT COUNT(*) FROM words WHERE account_id=? AND (status='mastered' OR known=1)", (a,)),
        }
        by_tier = {t: one("SELECT COUNT(*) FROM words WHERE account_id=? AND tier=?",
                          (a, t)) for t in ("D0", "D1", "D2")}
        days = [dict(r) for r in cxn.execute(
            "SELECT day,new_done,review_done,sent_done FROM log "
            "WHERE account_id=? ORDER BY day DESC LIMIT 30", (a,)).fetchall()]
        return jsonify({
            "phase": schedule.phase(cxn, a), "streak": schedule.streak(cxn, a),
            "activity_weeks": schedule.activity(cxn, a),
            "days": days, "today": lg,
            "by_status": by_status, "by_tier": by_tier, "words_total": total,
            "contexts": one("SELECT COUNT(*) FROM contexts c JOIN papers p ON p.id=c.paper_id WHERE p.account_id=?", (a,)),
            "papers": one("SELECT COUNT(*) FROM papers WHERE account_id=?", (a,)),
        })

    # ---------- 设置 ----------
    KEYS = ["daily_new", "daily_sent", "review_cap", "port",
            "trans_provider", "trans_key", "seed_terms", "oa_download_limit",
            "min_level", "llm_base_url", "llm_api_key", "llm_model",
            "auto_speak", "repeat_per_session"]

    @app.get("/api/settings")
    def api_settings():
        cxn = conn()
        a = acct()
        out = {k: get_setting(cxn, a, k) for k in KEYS}
        u = user_row(cxn, uid=a)
        out["username"] = u["username"]
        out["direction"] = u["direction"]
        return jsonify(out)

    @app.post("/api/settings")
    def api_settings_save():
        cxn = conn()
        data = request.get_json(force=True) or {}
        for k, v in data.items():
            if k in KEYS:
                set_setting(cxn, acct(), k, str(v))
        cxn.commit()
        return jsonify({"ok": True})

    # ---------- 后台（管理员） ----------
    @app.get("/api/admin/users")
    def api_admin_users():
        return jsonify({"users": [dict(r) for r in list_users(conn())]})

    @app.post("/api/admin/users")
    def api_admin_create():
        cxn = conn()
        data = request.get_json(force=True) or {}
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        if not username or len(password) < 6:
            return jsonify({"error": "需要用户名且密码至少 6 位"}), 400
        try:
            uid = create_user(cxn, username, password,
                              direction=data.get("direction") or "",
                              is_admin=1 if data.get("is_admin") else 0,
                              note=data.get("note") or "")
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        # 为新账号按方向写默认抓取词与默认配额
        set_setting(cxn, uid, "seed_terms", directions.default_terms(
            data.get("direction") or ""))
        set_setting(cxn, uid, "daily_new", "15")
        set_setting(cxn, uid, "daily_sent", "2")
        set_setting(cxn, uid, "review_cap", "60")
        cxn.commit()
        return jsonify({"ok": True, "id": uid})

    @app.post("/api/admin/users/reset")
    def api_admin_reset():
        data = request.get_json(force=True) or {}
        pwd = data.get("password") or ""
        if len(pwd) < 6:
            return jsonify({"error": "密码至少 6 位"}), 400
        reset_password(conn(), int(data.get("id", 0)), pwd)
        return jsonify({"ok": True})

    @app.post("/api/admin/users/delete")
    def api_admin_delete():
        data = request.get_json(force=True) or {}
        try:
            delete_user(conn(), int(data.get("id", 0)))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"ok": True})

    return app


if __name__ == "__main__":
    a = create_app()
    port = int(get_setting(connect(), 1, "port", "5010"))
    a.run(host="127.0.0.1", port=port, debug=False)
