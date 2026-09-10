#!/usr/bin/env python3
"""Science English - 命令行入口（支持按账号操作）.

用法:
  uv run manage.py init            # 建库/迁移 + 导入词典(若缺)
  uv run manage.py fetch [--account english|1] [--terms '...']
  uv run manage.py oa     [--account ...]
  uv run manage.py import-pdf [--account ...] <PDF或文件夹>...
  uv run manage.py extract [--account ...]
  uv run manage.py phonetic
  uv run manage.py ipa
  uv run manage.py status [--account ...]
  uv run manage.py serve
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import corpus, directions, gloss, ingest  # noqa: E402
from lib.db import (connect, get_setting, init_db, set_setting,  # noqa: E402
                    user_row, today)
from lib.paths import INBOX, ensure_dirs  # noqa: E402


def resolve_account(conn, name: str) -> int:
    if name is None:
        return 1
    u = user_row(conn, name) if not name.isdigit() else user_row(conn, uid=int(name))
    if not u:
        print(f"账号不存在: {name}", file=sys.stderr)
        raise SystemExit(1)
    return u["id"]


def account_terms(conn, account_id: int) -> str:
    """Default fetch keywords: account's own setting (set on creation) else direction."""
    u = user_row(conn, uid=account_id)
    terms = conn.execute(
        "SELECT value FROM settings WHERE account_id=? AND key='seed_terms'",
        (account_id,)).fetchone()
    if terms and terms["value"]:
        return terms["value"]
    return directions.default_terms(u["direction"] if u else "")


def cmd_init(args: argparse.Namespace) -> int:
    init_db()
    ensure_dirs()
    print("数据库就绪:", (Path(__file__).parent / "data/lit.db").resolve())
    if args.no_dict:
        print("跳过词典下载(可用 manage.py dict 补)")
        return 0
    return cmd_dict(args)


def cmd_dict(args: argparse.Namespace) -> int:
    conn = connect()
    path = gloss.download_ecdict(progress=not args.quiet)
    n = gloss.import_ecdict(conn, path)
    print(f"英汉词典已就绪: {n} 词条")
    return 0


def cmd_phonetic(args: argparse.Namespace) -> int:
    init_db()
    from lib.db import backfill_phonetics, ensure_columns
    conn = connect()
    ensure_columns(conn)
    n = backfill_phonetics(conn)
    print(f"已为 {n} 个词回填音标")
    return 0


def cmd_ipa(args: argparse.Namespace) -> int:
    init_db()
    conn = connect()
    n = gloss.import_ipa(conn)
    print(f"en_US 音标表已就绪: {n} 词条")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    conn = connect()
    account = resolve_account(conn, args.account)
    terms = (args.terms if args.terms is not None else account_terms(conn, account))
    terms = [t.strip() for t in terms.replace(",", "|").split("|") if t.strip()]
    if not terms and args.source:
        terms = [""]   # 仅按期刊来源抓取
    if not terms:
        print("未指定检索词且未指定 --source，无抓取内容", file=sys.stderr)
        return 1
    added = corpus.fetch_into_db(conn, terms, account_id=account,
                                 target_new=args.limit, source=args.source)
    where = f"来源 {args.source}" if args.source else ("主题 " + " | ".join(terms))
    print(f"账号#{account} 从{where} 新增文献 {added} 篇（随后运行 manage.py extract 分词入库）")
    return 0


def cmd_oa(args: argparse.Namespace) -> int:
    conn = connect()
    account = resolve_account(conn, args.account)
    n = ingest.download_oa_fulltext(conn, account, limit=args.limit)
    print(f"已下载解析 {n} 篇开放获取全文")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    conn = connect()
    account = resolve_account(conn, args.account)
    files: list[Path] = []
    for arg in args.paths or [INBOX]:
        p = Path(arg)
        if p.is_dir():
            files.extend(sorted(p.glob("*.pdf")))
        elif p.exists() and p.suffix.lower() == ".pdf":
            files.append(p)
    total = 0
    for f in files:
        total += ingest.ingest_local_pdf(conn, account, str(f))
    print(f"账号#{account} 导入完成: {len(files)} 个PDF, 新增词条行 {total}")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    conn = connect()
    account = resolve_account(conn, args.account)
    ingest.extract_pending(conn, account, limit=args.limit)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    conn = connect()
    account = resolve_account(conn, args.account)
    u = user_row(conn, uid=account)

    def one(q, a=()):
        return conn.execute(q, a).fetchone()[0]

    npapers = one("SELECT COUNT(*) FROM papers WHERE account_id=?", (account,))
    nextr = one("SELECT COUNT(*) FROM papers WHERE account_id=? AND extracted=1", (account,))
    nwords = one("SELECT COUNT(*) FROM words WHERE account_id=?", (account,))
    d0 = one("SELECT COUNT(*) FROM words WHERE account_id=? AND tier='D0'", (account,))
    d1 = one("SELECT COUNT(*) FROM words WHERE account_id=? AND tier='D1'", (account,))
    nctx = one("SELECT COUNT(*) FROM contexts c JOIN papers p ON p.id=c.paper_id "
               "WHERE p.account_id=?", (account,))
    glossed = one("SELECT COUNT(*) FROM words WHERE account_id=? AND gloss_zh!=''", (account,))
    known = one("SELECT COUNT(*) FROM words WHERE account_id=? AND known=1", (account,))
    days = one("SELECT COUNT(*) FROM log WHERE account_id=?", (account,))
    print(f"日期: {today()}   账号: {u['username']}（{u['direction']}）")
    print(f"文献总数: {npapers}  已分词: {nextr}")
    print(f"词库词条: {nwords}  D0领域: {d0}  D1学术: {d1}")
    print(f"例句: {nctx}  有中文释义: {glossed}  已认识: {known}")
    print(f"学习记录天数: {days}")
    return 0


def cmd_level(args: argparse.Namespace) -> int:
    """按账号目标难度档重筛：低于该档的词标记为已认识（从学习队列移除）。"""
    conn = connect()
    account = resolve_account(conn, args.account)
    level = args.level if args.level in ("cet4", "cet6", "ky") else "cet6"
    set_setting(conn, account, "min_level", level)
    extra = gloss.extra_tags(level)
    rows = conn.execute(
        "SELECT w.id, d.tag FROM words w "
        "LEFT JOIN dict d ON d.word=w.word "
        "WHERE w.account_id=? AND w.known=0", (account,)).fetchall()
    ids = [r["id"] for r in rows
           if r["tag"] and (set((r["tag"] or "").split()) & extra)]
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        ph = ",".join("?" * len(chunk))
        conn.execute(
            f"UPDATE words SET known=1, status='mastered' WHERE id IN ({ph})",
            chunk)
        conn.execute(f"DELETE FROM reviews WHERE word_id IN ({ph})", chunk)
    conn.commit()
    tot = conn.execute(
        "SELECT COUNT(*) FROM words WHERE account_id=?", (account,)).fetchone()[0]
    left = conn.execute(
        "SELECT COUNT(*) FROM words WHERE account_id=? AND known=0",
        (account,)).fetchone()[0]
    print(f"账号#{account} 难度档设为 {level}："
          f"移除低档词 {len(ids)} 个；剩余待学词 {left} / 词库总数 {tot}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from app import create_app
    app = create_app()
    port = args.port or int(get_setting(connect(), 1, "port", "5010"))
    print(f"\n Science English 已启动: http://127.0.0.1:{port}\n 本地使用 Ctrl+C 退出\n")
    app.run(host="127.0.0.1", port=port, debug=args.debug)


def add_account_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--account", default=None,
                   help="账号用户名或ID（默认 1=english）")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="manage.py", description="Science English 多账号")
    sub = p.add_subparsers(dest="cmd", required=True)

    ip = sub.add_parser("init", help="建库迁移并下载词典")
    ip.add_argument("--no-dict", action="store_true")
    ip.add_argument("--quiet", action="store_true")

    d = sub.add_parser("dict")
    d.add_argument("--quiet", action="store_true")
    f = sub.add_parser("fetch"); add_account_arg(f)
    f.add_argument("--terms", default=None,
                   help="检索词（| 分隔）；留空则仅按来源抓取")
    f.add_argument("--source", default=None, help="限定期刊/来源，如 \"Nature Food\"")
    f.add_argument("--limit", type=int, default=300)
    o = sub.add_parser("oa"); add_account_arg(o)
    o.add_argument("--limit", type=int, default=40)
    i = sub.add_parser("import-pdf"); add_account_arg(i)
    i.add_argument("paths", nargs="*")
    e = sub.add_parser("extract"); add_account_arg(e)
    e.add_argument("--limit", type=int, default=0)
    st = sub.add_parser("status"); add_account_arg(st)
    sub.add_parser("phonetic")
    sub.add_parser("ipa")
    lv = sub.add_parser("level", help="按目标难度档重筛某账号词库")
    add_account_arg(lv)
    lv.add_argument("--level", choices=["cet4", "cet6", "ky"], default="cet6")
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=0)
    s.add_argument("--debug", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    handlers = {"init": cmd_init, "dict": cmd_dict, "fetch": cmd_fetch,
                "oa": cmd_oa, "import-pdf": cmd_import, "extract": cmd_extract,
                "status": cmd_status, "phonetic": cmd_phonetic,
                "ipa": cmd_ipa, "level": cmd_level, "serve": cmd_serve}
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
