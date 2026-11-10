"""Transactional, isolated memory banks with versioned checkpoints."""

from __future__ import annotations

import base64
import zlib
import hashlib
import json
import math
import re
import sqlite3
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .scene_policy import normalize as normalize_scene, DEFAULT as DEFAULT_SCENE_POLICY, IMMERSIVE

STATUSES = {"active", "dormant", "archived", "superseded", "expired", "forgotten"}
KINDS = {"episodic", "factual", "relational", "preference", "planned", "unknown"}
TTL = {
    "episodic": 7,
    "factual": 180,
    "relational": 90,
    "preference": 60,
    "planned": 2,
    "unknown": 30,
}
DEFAULTS = {
    "auto_summary": True,
    "trigger_rounds": 10,
    "split_by_day": True,
    "gap_minutes": 180,
    "daily_decay": 0.01,
    "protected_importance": 1.0,
    "cleanup_days": 30,
    "cleanup_importance": 0.3,
    "auto_archive": True,
    "dedup_similarity": 0.92,
    "focus": "",
    "timezone": "Asia/Shanghai",
    "style_learning": True,
    "scene_memory_policy": DEFAULT_SCENE_POLICY,
}


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def pack_snapshot(value: dict) -> str:
    return "z:" + base64.b64encode(zlib.compress(dumps(value).encode(), 6)).decode()


def unpack_snapshot(value: str) -> dict:
    return (
        json.loads(zlib.decompress(base64.b64decode(value[2:])).decode())
        if value.startswith("z:")
        else json.loads(value)
    )


def tokens(text: str) -> set[str]:
    text = re.sub(r"\s+", "", str(text).lower())
    result = set(re.findall(r"[a-z0-9_]{2,}", text))
    result.update(
        text[i : i + 2]
        for i in range(len(text) - 1)
        if re.fullmatch(r"[\u4e00-\u9fff]{2}", text[i : i + 2])
    )
    return result


def cosine(left: list, right: list) -> float:
    if not left or len(left) != len(right):
        return 0.0
    norm = math.sqrt(sum(x * x for x in left) * sum(x * x for x in right))
    return max(0.0, sum(x * y for x, y in zip(left, right)) / norm) if norm else 0.0


def number(value: Any, low=0.0, high=1.0) -> float:
    result = float(value)
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f"数值必须在 {low} 到 {high} 之间")
    return result


def validate_summary(data: dict) -> dict:
    """Validate model output before it can advance a summary checkpoint."""
    if not isinstance(data, dict):
        raise ValueError("总结模型没有返回 JSON 对象")
    summary = str(data.get("summary") or "").strip()
    if not summary or len(summary) > 12000:
        raise ValueError("总结内容为空或过长")
    result = {
        "summary": summary,
        "importance": number(data.get("importance", 0.5)),
        "kind": str(data.get("kind", "episodic")).lower(),
    }
    if result["kind"] not in KINDS:
        raise ValueError("未知记忆类型")
    for key in ("topics", "participants", "key_facts"):
        values = data.get(key, [])
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise ValueError(f"{key} 必须是文字列表")
        result[key] = list(dict.fromkeys(v.strip()[:500] for v in values if v.strip()))[
            :20
        ]
    atoms = data.get("atoms") or []
    if not isinstance(atoms, list):
        raise ValueError("atoms 必须是列表")
    result["atoms"] = []
    for atom in atoms[:40]:
        if not isinstance(atom, dict) or not str(atom.get("content", "")).strip():
            raise ValueError("记忆原子缺少内容")
        kind = str(atom.get("kind", "unknown"))
        if kind not in KINDS:
            raise ValueError("未知原子类型")
        result["atoms"].append(
            {
                "content": str(atom["content"])[:2000],
                "kind": kind,
                "importance": number(atom.get("importance", result["importance"])),
            }
        )
    relations = data.get("relations", [])
    if not isinstance(relations, list):
        raise ValueError("relations 必须是列表")
    result["relations"] = []
    for relation in relations[:30]:
        if not isinstance(relation, dict) or not all(
            isinstance(relation.get(k), str) and relation[k].strip()
            for k in ("source", "target", "relation")
        ):
            raise ValueError("关系必须包含 source、target 和 relation")
        result["relations"].append(
            {k: relation[k][:300] for k in ("source", "target", "relation")}
        )
    return result


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Engine:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS banks(id TEXT PRIMARY KEY,name TEXT NOT NULL,
              routes TEXT NOT NULL,settings TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0,
              cursor INTEGER NOT NULL DEFAULT 0,generation INTEGER NOT NULL DEFAULT 0,baseline TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS routes(route TEXT PRIMARY KEY,bank TEXT NOT NULL REFERENCES banks(id));
            CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,bank TEXT NOT NULL,
              source TEXT NOT NULL,event_key TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,
              created REAL NOT NULL,host TEXT,metadata TEXT NOT NULL,
              UNIQUE(bank,source,event_key,role));
            CREATE INDEX IF NOT EXISTS message_bank ON messages(bank,id);
            CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY,bank TEXT NOT NULL,node INTEGER,
              content TEXT NOT NULL,importance REAL NOT NULL,kind TEXT NOT NULL,status TEXT NOT NULL,
              created REAL NOT NULL,updated REAL NOT NULL,accesses INTEGER NOT NULL DEFAULT 0,
              accessed REAL NOT NULL DEFAULT 0,metadata TEXT NOT NULL,vector TEXT NOT NULL DEFAULT '[]');
            CREATE INDEX IF NOT EXISTS memory_bank ON memories(bank,created);
            CREATE TABLE IF NOT EXISTS nodes(id INTEGER PRIMARY KEY AUTOINCREMENT,bank TEXT NOT NULL,
              cursor INTEGER NOT NULL,title TEXT NOT NULL,created REAL NOT NULL,snapshot TEXT NOT NULL,
              host TEXT NOT NULL,source_start INTEGER NOT NULL,trigger TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS node_bank ON nodes(bank,id);
            CREATE TABLE IF NOT EXISTS rollback_journal(bank TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS datasets(bank TEXT NOT NULL,name TEXT NOT NULL,payload TEXT NOT NULL,
              PRIMARY KEY(bank,name));
            CREATE TABLE IF NOT EXISTS imported(bank TEXT NOT NULL,source TEXT NOT NULL,
              source_id TEXT NOT NULL,digest TEXT NOT NULL,PRIMARY KEY(bank,source,source_id));
            """)
        self.path.chmod(0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _bank(self, db, bank: str) -> dict:
        row = db.execute("SELECT * FROM banks WHERE id=?", (bank,)).fetchone()
        if row is None:
            raise ValueError("这套记忆不存在")
        return {
            **dict(row),
            "routes": json.loads(row["routes"]),
            "settings": {**DEFAULTS, **json.loads(row["settings"])},
            "baseline": json.loads(row["baseline"]),
        }

    def bank(self, bank: str) -> dict:
        with self.connect() as db:
            return self._bank(db, bank)

    def banks(self) -> list[dict]:
        with self.connect() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM banks ORDER BY created")]
        return [self.state(i, include_memories=False) for i in ids]

    def create_bank(
        self, name: str, routes: list[str], baseline: dict | None = None, settings=None
    ) -> str:
        name = str(name).strip()
        if not name or len(name) > 100 or not isinstance(routes, list) or not routes:
            raise ValueError("请填写名称和至少一个明确的来源")
        routes = list(dict.fromkeys(str(r).strip() for r in routes))
        for route in routes:
            if not (
                re.fullmatch(r"[^:]+:FriendMessage:\d+", route)
                or re.fullmatch(r"group:\d+", route)
                or route in {"xiaoheihe:public", "bilibili:public", "bilibili:self"}
                or re.fullmatch(r"bilibili:dm:\d+", route)
            ):
                raise ValueError(f"不支持的来源：{route}")
        if any(":FriendMessage:" in r for r in routes) and len(routes) != 1:
            raise ValueError("QQ 私聊必须独占一套记忆，不能与其他来源共用")
        ident = uuid.uuid4().hex
        with self.transaction() as db:
            for route in routes:
                if db.execute(
                    "SELECT 1 FROM routes WHERE route=?", (route,)
                ).fetchone():
                    raise ValueError(f"来源 {route} 已绑定另一套记忆，禁止共享")
            db.execute(
                "INSERT INTO banks(id,name,routes,settings,revision,cursor,baseline,created) VALUES(?,?,?,?,0,0,?,?)",
                (
                    ident,
                    name,
                    dumps(routes),
                    dumps({**DEFAULTS, **(settings or {})}),
                    dumps(baseline or {}),
                    time.time(),
                ),
            )
            db.executemany(
                "INSERT INTO routes VALUES(?,?)", [(r, ident) for r in routes]
            )
        return ident

    def route(self, source: str) -> str | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT bank FROM routes WHERE route=?", (source,)
            ).fetchone()
            return row[0] if row else None

    def _writable(self, db, bank):
        if db.execute(
            "SELECT 1 FROM rollback_journal WHERE bank=?", (bank,)
        ).fetchone():
            raise ValueError("这套记忆正在恢复回退，请稍后重试")

    def configure(self, bank: str, name: str, settings: dict):
        merged = {**self.bank(bank)["settings"], **settings}
        for key, low, high in [
            ("trigger_rounds", 1, 100),
            ("gap_minutes", 0, 1440),
            ("daily_decay", 0, 1),
            ("cleanup_days", 1, 3650),
            ("cleanup_importance", 0, 1),
            ("dedup_similarity", 0.5, 1),
            ("protected_importance", 0, 1),
        ]:
            merged[key] = number(merged[key], low, high)
        if merged["scene_memory_policy"] not in {DEFAULT_SCENE_POLICY, IMMERSIVE}:
            raise ValueError("未知场景记忆口径")
        if merged["scene_memory_policy"] == IMMERSIVE and not all(
            re.fullmatch(r"[^:]+:FriendMessage:\d+", route) for route in self.bank(bank)["routes"]
        ):
            raise ValueError("沉浸式记忆仅适用于独占的QQ私聊记忆空间")
        ZoneInfo(merged["timezone"])
        if not str(name).strip() or len(str(name)) > 100:
            raise ValueError("记忆名称应为 1—100 个字")
        for key in ("auto_summary", "auto_archive", "split_by_day", "style_learning"):
            if not isinstance(merged[key], bool):
                raise ValueError(f"{key} 必须是开关")
        with self.transaction() as db:
            self._writable(db, bank)
            db.execute(
                "UPDATE banks SET name=?,settings=?,revision=revision+1 WHERE id=?",
                (name.strip(), dumps(merged), bank),
            )

    def append(
        self,
        bank: str,
        source: str,
        key: str,
        role: str,
        content: str,
        *,
        timestamp=None,
        host=None,
        metadata=None,
        expected_revision=None,
        expected_generation=None,
    ) -> bool:
        content = str(content or "").strip()
        if not content or role not in {"user", "assistant", "memory", "reflection"}:
            return False
        with self.transaction() as db:
            info = self._bank(db, bank)
            self._writable(db, bank)
            if source not in info["routes"]:
                raise PermissionError("来源与这套记忆不匹配")
            if expected_generation is not None and info["generation"] != expected_generation:
                raise ValueError("integration_generation_changed")
            if expected_revision is not None and info["revision"] != expected_revision:
                return False
            inserted = db.execute(
                "INSERT OR IGNORE INTO messages(bank,source,event_key,role,content,created,host,metadata) VALUES(?,?,?,?,?,?,?,?)",
                (
                    bank,
                    source,
                    str(key),
                    role,
                    content[:32000],
                    timestamp or time.time(),
                    dumps(host) if host is not None else None,
                    dumps(metadata or {}),
                ),
            ).rowcount
        return bool(inserted)

    def pending(self, bank: str, *, limit=100000) -> list[dict]:
        with self.connect() as db:
            info = self._bank(db, bank)
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM messages WHERE bank=? AND id>? ORDER BY id LIMIT ?",
                    (bank, info["cursor"], limit),
                )
            ]

    def plan(self, bank: str, *, manual=False, ceiling=None) -> dict | None:
        info = self.bank(bank)
        rows = self.pending(bank)
        if ceiling is not None:
            rows = [r for r in rows if r["id"] <= ceiling]
        if not rows:
            return None
        if not manual and (
            not info["settings"]["auto_summary"]
            or len(rows) < info["settings"]["trigger_rounds"] * 2
        ):
            return None
        # Never checkpoint a private user's message while its answer is still running.
        if len(info["routes"]) == 1 and ":FriendMessage:" in info["routes"][0]:
            while rows and rows[-1]["role"] != "assistant":
                rows.pop()
        if not rows:
            return None
        settings = info["settings"]
        zone = ZoneInfo(settings["timezone"])
        for i in range(2, len(rows)):
            prev, current = rows[i - 1], rows[i]
            day_split = (
                settings["split_by_day"]
                and datetime.fromtimestamp(prev["created"], zone).date()
                != datetime.fromtimestamp(current["created"], zone).date()
            )
            gap_split = (
                settings["gap_minutes"] > 0
                and current["created"] - prev["created"] > settings["gap_minutes"] * 60
            )
            if (day_split or gap_split) and current["role"] != "assistant":
                rows = rows[:i]
                break
        # Bound model input without ever advancing past the portion actually sent.
        size = 0
        end = len(rows)
        for i, row in enumerate(rows):
            size += len(row["content"])
            if size > 48000 and i >= 2 and row["role"] != "assistant":
                end = i
                break
        rows = rows[:end]
        host = self.host_at(bank, rows[-1]["id"])
        return {
            "bank": bank,
            "revision": info["revision"],
            "start": info["cursor"],
            "end": rows[-1]["id"],
            "messages": rows,
            "host": host,
            "settings": settings,
            "trigger": "manual" if manual else "auto",
        }

    def host_at(self, bank: str, end: int) -> dict:
        # Each source carries its own last host snapshot. A public bank can own several sources.
        info = self.bank(bank)
        result = dict(info["baseline"])
        with self.connect() as db:
            rows = db.execute(
                "SELECT source,host FROM messages WHERE bank=? AND id<=? AND host IS NOT NULL ORDER BY id",
                (bank, end),
            )
            for row in rows:
                result[row["source"]] = json.loads(row["host"])
        return result

    def _snapshot(self, db, bank) -> dict:
        return {
            "memories": [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM memories WHERE bank=? ORDER BY created,id", (bank,)
                )
            ],
            "datasets": [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM datasets WHERE bank=? ORDER BY name", (bank,)
                )
            ],
        }

    def commit(self, plan: dict, data: dict, vector=None) -> int | None:
        data = validate_summary(data)
        now = time.time()
        for atom in data["atoms"]:
            atom.update(created=now, status="active", ttl_days=TTL[atom["kind"]])
        with self.transaction() as db:
            bank = self._bank(db, plan["bank"])
            self._writable(db, bank["id"])
            normalized = validate_summary(normalize_scene(bank, data))
            if normalized["summary"] != data["summary"]:
                vector = []
            data = normalized
            for atom in data["atoms"]:
                atom.update(created=now, status="active", ttl_days=TTL[atom["kind"]])
            if bank["revision"] != plan["revision"] or bank["cursor"] != plan["start"]:
                return None
            last = (
                db.execute(
                    "SELECT MAX(id) FROM messages WHERE bank=?", (bank["id"],)
                ).fetchone()[0]
                or 0
            )
            if plan["end"] > last:
                return None
            cursor = db.execute(
                "INSERT INTO nodes(bank,cursor,title,created,snapshot,host,source_start,trigger) VALUES(?,?,?,?,?,?,?,?)",
                (
                    bank["id"],
                    plan["end"],
                    data["summary"][:100],
                    now,
                    "{}",
                    dumps(plan["host"]),
                    plan["start"],
                    plan["trigger"],
                ),
            )
            node = cursor.lastrowid
            metadata = {
                **data,
                "source_window": {"start": plan["start"], "end": plan["end"]},
                "source_start_time": plan["messages"][0]["created"],
                "source_end_time": plan["messages"][-1]["created"],
            }
            near = None
            new_tokens = tokens(data["summary"])
            for old in db.execute(
                "SELECT * FROM memories WHERE bank=? AND status='active' ORDER BY updated DESC LIMIT 500",
                (bank["id"],),
            ):
                old_tokens = tokens(old["content"])
                similarity = len(new_tokens & old_tokens) / max(
                    1, len(new_tokens | old_tokens)
                )
                if similarity >= bank["settings"]["dedup_similarity"]:
                    near = old
                    break
            if near:
                previous = json.loads(near["metadata"])
                metadata["source_windows"] = (
                    previous.get("source_windows", [previous.get("source_window")])
                    + [metadata["source_window"]]
                )[-50:]
                metadata["atoms"] = previous.get("atoms", []) + metadata["atoms"]
                metadata["relations"] = (
                    previous.get("relations", []) + metadata["relations"]
                )
                for key in ("topics", "participants", "key_facts"):
                    metadata[key] = list(
                        dict.fromkeys(previous.get(key, []) + metadata[key])
                    )[:100]
                db.execute(
                    "UPDATE memories SET importance=?,updated=?,metadata=?,vector=? WHERE id=? AND bank=?",
                    (
                        max(near["importance"], data["importance"]),
                        now,
                        dumps(metadata),
                        dumps(vector or []),
                        near["id"],
                        bank["id"],
                    ),
                )
            else:
                db.execute(
                    "INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?,0,0,?,?)",
                    (
                        uuid.uuid4().hex,
                        bank["id"],
                        node,
                        data["summary"],
                        data["importance"],
                        data["kind"],
                        "active",
                        now,
                        now,
                        dumps(metadata),
                        dumps(vector or []),
                    ),
                )
            db.execute(
                "UPDATE banks SET cursor=?,revision=revision+1 WHERE id=?",
                (plan["end"], bank["id"]),
            )
            db.execute(
                "UPDATE nodes SET snapshot=? WHERE id=? AND bank=?",
                (pack_snapshot(self._snapshot(db, bank["id"])), node, bank["id"]),
            )
            return node

    def memories(self, bank: str) -> list[dict]:
        with self.connect() as db:
            self._bank(db, bank)
            return [
                {
                    **dict(r),
                    "metadata": json.loads(r["metadata"]),
                    "vector": json.loads(r["vector"]),
                }
                for r in db.execute(
                    "SELECT * FROM memories WHERE bank=? ORDER BY created DESC", (bank,)
                )
            ]

    def state(self, bank: str, *, include_memories=True) -> dict:
        info = self.bank(bank)
        with self.connect() as db:
            total = db.execute(
                "SELECT count(*) FROM messages WHERE bank=?", (bank,)
            ).fetchone()[0]
            pending = db.execute(
                "SELECT count(*) FROM messages WHERE bank=? AND id>?",
                (bank, info["cursor"]),
            ).fetchone()[0]
            nodes = [
                dict(r)
                for r in db.execute(
                    "SELECT id,title,created,cursor,source_start,trigger FROM nodes WHERE bank=? ORDER BY id DESC",
                    (bank,),
                )
            ]
            recovering = (
                db.execute(
                    "SELECT 1 FROM rollback_journal WHERE bank=?", (bank,)
                ).fetchone()
                is not None
            )
        memories = self.memories(bank)
        return {
            "id": bank,
            "name": info["name"],
            "routes": info["routes"],
            "settings": info["settings"],
            "revision": info["revision"],
            "generation": info["generation"],
            "cursor": info["cursor"],
            "message_count": total,
            "pending_count": pending,
            "memory_count": len(memories),
            "statuses": dict(Counter(m["status"] for m in memories)),
            "nodes": nodes,
            "memories": memories if include_memories else [],
            "recovering": recovering,
        }

    def update_memory(self, bank: str, ident: str, *, importance: float, status: str):
        if status not in STATUSES:
            raise ValueError("未知的记忆状态")
        with self.transaction() as db:
            self._writable(db, bank)
            if not db.execute(
                "UPDATE memories SET importance=?,status=?,updated=? WHERE bank=? AND id=?",
                (number(importance), status, time.time(), bank, ident),
            ).rowcount:
                raise ValueError("记忆不存在或不属于当前空间")
            db.execute("UPDATE banks SET revision=revision+1 WHERE id=?", (bank,))

    def preview_rollback(self, bank: str, node: int) -> dict:
        with self.connect() as db:
            info = self._bank(db, bank)
            self._writable(db, bank)
            selected = db.execute(
                "SELECT * FROM nodes WHERE bank=? AND id=?", (bank, node)
            ).fetchone()
            if selected is None:
                raise ValueError("总结节点不存在或不属于当前空间")
            previous = db.execute(
                "SELECT * FROM nodes WHERE bank=? AND id<? ORDER BY id DESC LIMIT 1",
                (bank, node),
            ).fetchone()
            end = previous["cursor"] if previous else 0
            snapshot = (
                unpack_snapshot(previous["snapshot"])
                if previous
                else {"memories": [], "datasets": []}
            )
            message_end = snapshot.get("retained_end", end)
            removed = db.execute(
                "SELECT count(*) FROM nodes WHERE bank=? AND id>=?", (bank, node)
            ).fetchone()[0]
            messages = db.execute(
                "SELECT count(*) FROM messages WHERE bank=? AND id>?",
                (bank, message_end),
            ).fetchone()[0]
            return {
                "bank": bank,
                "node": node,
                "revision": info["revision"],
                "target_node": previous["id"] if previous else None,
                "target_title": previous["title"] if previous else "初始状态",
                "cursor": end,
                "message_end": message_end,
                "removed_nodes": removed,
                "removed_messages": messages,
                "host": json.loads(previous["host"]) if previous else info["baseline"],
                "snapshot": snapshot,
            }

    def begin_rollback(self, bank: str, node: int, expected_revision: int) -> dict:
        preview = self.preview_rollback(bank, node)
        with self.transaction() as db:
            info = self._bank(db, bank)
            self._writable(db, bank)
            if (
                info["revision"] != expected_revision
                or preview["revision"] != expected_revision
            ):
                raise ValueError("记忆已变化，请重新预览后确认删除")
            db.execute(
                "INSERT INTO rollback_journal VALUES(?,?)", (bank, dumps(preview))
            )
            db.execute(
                "UPDATE banks SET revision=revision+1,generation=generation+1 WHERE id=?",
                (bank,),
            )
        return preview

    def finish_rollback(self, bank: str) -> dict:
        with self.transaction() as db:
            row = db.execute(
                "SELECT payload FROM rollback_journal WHERE bank=?", (bank,)
            ).fetchone()
            if not row:
                raise ValueError("没有待完成的回退")
            plan = json.loads(row[0])
            db.execute("DELETE FROM memories WHERE bank=?", (bank,))
            for memory in plan["snapshot"]["memories"]:
                cols = list(memory)
                db.execute(
                    f"INSERT INTO memories({','.join(cols)}) VALUES({','.join('?' for _ in cols)})",
                    tuple(memory.values()),
                )
            db.execute("DELETE FROM datasets WHERE bank=?", (bank,))
            for dataset in plan["snapshot"].get("datasets", []):
                db.execute(
                    "INSERT INTO datasets VALUES(?,?,?)",
                    (bank, dataset["name"], dataset["payload"]),
                )
            db.execute(
                "DELETE FROM messages WHERE bank=? AND id>?",
                (bank, plan.get("message_end", plan["cursor"])),
            )
            db.execute("DELETE FROM nodes WHERE bank=? AND id>=?", (bank, plan["node"]))
            db.execute(
                "UPDATE banks SET cursor=?,revision=revision+1 WHERE id=?",
                (plan["cursor"], bank),
            )
            db.execute("DELETE FROM rollback_journal WHERE bank=?", (bank,))
        return plan

    def journals(self) -> list[dict]:
        with self.connect() as db:
            return [
                json.loads(r[0])
                for r in db.execute("SELECT payload FROM rollback_journal")
            ]

    def graph(self, bank: str, *, limit=300) -> dict:
        memories = [m for m in self.memories(bank) if m["status"] == "active"]
        nodes, edges = {}, []
        for memory in memories[:limit]:
            ident = "memory:" + memory["id"]
            nodes[ident] = {
                "id": ident,
                "label": memory["content"][:30],
                "kind": "memory",
                "memory_id": memory["id"],
            }
            metadata = memory["metadata"]
            for key, kind in [
                ("topics", "topic"),
                ("participants", "person"),
                ("key_facts", "fact"),
            ]:
                for value in metadata.get(key, [])[:10]:
                    entity = kind + ":" + value
                    nodes[entity] = {"id": entity, "label": value, "kind": kind}
                    edges.append(
                        {
                            "source": ident,
                            "target": entity,
                            "label": {
                                "topic": "涉及话题",
                                "person": "涉及人物",
                                "fact": "记录事实",
                            }[kind],
                        }
                    )
            for relation in metadata.get("relations", []):
                if relation.get("status", "active") != "active":
                    continue
                a, b = "entity:" + relation["source"], "entity:" + relation["target"]
                nodes[a] = {"id": a, "label": relation["source"], "kind": "fact"}
                nodes[b] = {"id": b, "label": relation["target"], "kind": "fact"}
                edges.append({"source": a, "target": b, "label": relation["relation"]})
                edges.append({"source": ident, "target": a, "label": "关系来源"})
        return {
            "nodes": list(nodes.values()),
            "edges": edges,
            "shown_memories": min(limit, len(memories)),
            "total_memories": len(memories),
        }

    def recall(
        self, bank: str, query: str, *, limit=5, vector=None, track=False
    ) -> dict:
        query = str(query).strip()[:1000]
        if not query:
            raise ValueError("请写一句你可能会问的话")
        memories = [m for m in self.memories(bank) if m["status"] == "active"]
        q = tokens(query)
        candidates = []
        graph_matches = set()
        for m in memories:
            fields = m["metadata"]
            entities = (
                fields.get("topics", [])
                + fields.get("participants", [])
                + fields.get("key_facts", [])
            )
            for entity in entities:
                if (q & tokens(entity)) or (len(entity) > 1 and entity in query):
                    graph_matches.add(entity)
        for m in memories:
            content_tokens = tokens(
                m["content"] + " " + " ".join(m["metadata"].get("key_facts", []))
            )
            overlap = q & content_tokens
            lexical = len(overlap) / max(1, len(q))
            semantic = cosine(vector or [], m["vector"])
            entities = (
                m["metadata"].get("topics", [])
                + m["metadata"].get("participants", [])
                + m["metadata"].get("key_facts", [])
            )
            matched = list(graph_matches & set(entities))
            graph = min(1.0, len(matched) / max(1, min(3, len(entities))))
            relevance = max(lexical, semantic)
            if relevance <= 0 and not matched:
                continue
            score = (
                0.65 * relevance
                + 0.35 * graph
                + (0.08 if relevance > 0 and graph > 0 else 0)
            )
            score *= 0.7 + 0.3 * m["importance"]
            if semantic and not overlap and not matched and semantic < 0.45:
                continue
            candidates.append(
                {
                    "id": m["id"],
                    "content": m["content"],
                    "importance": m["importance"],
                    "status": m["status"],
                    "score": round(score, 4),
                    "matched_words": sorted(overlap)[:12],
                    "matched_entities": matched[:8],
                    "reason": (
                        "与你的问题有相同词语"
                        if overlap
                        else "语义接近"
                        if semantic
                        else "通过关联图谱找到"
                    )
                    + ("，并关联到 " + "、".join(matched[:3]) if matched else ""),
                    "node": m["node"],
                    "created": m["created"],
                }
            )
        selected = sorted(candidates, key=lambda m: m["score"], reverse=True)[
            : max(1, min(20, limit))
        ]
        if track and selected:
            with self.transaction() as db:
                self._writable(db, bank)
                db.executemany(
                    "UPDATE memories SET accesses=accesses+1,accessed=? WHERE bank=? AND id=?",
                    [(time.time(), bank, m["id"]) for m in selected],
                )
        return {
            "query": query,
            "matches": selected,
            "searched": len(memories),
            "excluded": len(self.memories(bank)) - len(memories),
            "explanation": "这些是正式聊天时会提供给模型的历史资料；这是检索预览，不会向 QQ 发消息，也不会增加访问次数。",
        }

    def maintain(self, now=None):
        now = now or time.time()
        with self.transaction() as db:
            for row in db.execute("SELECT id FROM banks").fetchall():
                bank = self._bank(db, row[0])
                if db.execute(
                    "SELECT 1 FROM rollback_journal WHERE bank=?", (bank["id"],)
                ).fetchone():
                    continue
                changed = False
                settings = bank["settings"]
                for m in db.execute(
                    "SELECT * FROM memories WHERE bank=?", (bank["id"],)
                ).fetchall():
                    metadata = json.loads(m["metadata"])
                    last = float(metadata.get("last_decay", m["created"]))
                    days = int((now - last) / 86400)
                    if days < 1:
                        continue
                    importance = m["importance"]
                    protection = (
                        min(10, m["accesses"]) / 10
                        if now - m["accessed"] < 30 * 86400
                        else 0
                    )
                    if importance < settings["protected_importance"]:
                        importance *= (
                            1 - settings["daily_decay"] * (1 - 0.8 * protection)
                        ) ** days
                    status = m["status"]
                    age = (now - m["created"]) / 86400
                    if (
                        settings["auto_archive"]
                        and status in {"active", "dormant"}
                        and age >= settings["cleanup_days"]
                        and importance < settings["cleanup_importance"]
                    ):
                        status = "archived"
                    for atom in metadata.get("atoms", []):
                        atom_age = (now - atom.get("created", m["created"])) / 86400
                        ttl = atom.get("ttl_days", 30)
                        if atom.get("status") in {
                            "active",
                            "dormant",
                            "expired",
                            "forgotten",
                        }:
                            atom["status"] = (
                                "forgotten"
                                if atom_age > ttl + 7
                                else "expired"
                                if atom_age > ttl
                                else "active"
                            )
                    metadata["last_decay"] = last + days * 86400
                    db.execute(
                        "UPDATE memories SET importance=?,status=?,metadata=?,accesses=? WHERE bank=? AND id=?",
                        (
                            importance,
                            status,
                            dumps(metadata),
                            int(m["accesses"] * 0.8**days),
                            bank["id"],
                            m["id"],
                        ),
                    )
                    changed = True
                if changed:
                    db.execute(
                        "UPDATE banks SET revision=revision+1 WHERE id=?", (bank["id"],)
                    )

    def dataset(self, bank: str, name: str, default=None):
        with self.connect() as db:
            self._bank(db, bank)
            row = db.execute(
                "SELECT payload FROM datasets WHERE bank=? AND name=?", (bank, name)
            ).fetchone()
            return json.loads(row[0]) if row else default

    def put_dataset(self, bank: str, name: str, value, *, revision=None):
        with self.transaction() as db:
            info = self._bank(db, bank)
            self._writable(db, bank)
            if revision is not None and info["revision"] != revision:
                return False
            encoded = dumps(value)
            old = db.execute(
                "SELECT payload FROM datasets WHERE bank=? AND name=?", (bank, name)
            ).fetchone()
            if old and old[0] == encoded:
                return True
            db.execute(
                "INSERT OR REPLACE INTO datasets VALUES(?,?,?)", (bank, name, encoded)
            )
            db.execute("UPDATE banks SET revision=revision+1 WHERE id=?", (bank,))
            return True

    def learnable(self, bank: str) -> list[dict]:
        previous = self.dataset(bank, "styles.json", {"cursor": 0})
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT id,content,source FROM messages WHERE bank=? AND role='user' AND id>? ORDER BY id LIMIT 100",
                    (bank, previous.get("cursor", 0)),
                )
            ]

    def import_memory(
        self,
        bank: str,
        source: str,
        source_id: str,
        content: str,
        *,
        metadata=None,
        importance=0.5,
        status="active",
        kind="episodic",
        created=None,
        vector=None,
    ):
        """Idempotently import legacy records without inventing historic checkpoints."""
        metadata = metadata or {}
        digest = hashlib.sha256(dumps([content, metadata]).encode()).hexdigest()
        key = hashlib.sha256(f"{bank}:{source}:{source_id}".encode()).hexdigest()
        with self.transaction() as db:
            self._writable(db, bank)
            info = self._bank(db, bank)
            new_content = normalize_scene(info, content)
            metadata = normalize_scene(info, metadata)
            if new_content != content:
                vector = []
            content = new_content
            found = db.execute(
                "SELECT digest FROM imported WHERE bank=? AND source=? AND source_id=?",
                (bank, source, str(source_id)),
            ).fetchone()
            if found:
                if found[0] != digest:
                    raise ValueError("导入来源已变化，不能覆盖已迁移记录")
                return False
            db.execute(
                "INSERT INTO memories VALUES(?,?,NULL,?,?,?,?,?,?,0,0,?,?)",
                (
                    key,
                    bank,
                    content,
                    number(importance),
                    kind if kind in KINDS else "unknown",
                    status if status in STATUSES else "archived",
                    created or time.time(),
                    created or time.time(),
                    dumps(metadata),
                    dumps(vector or []),
                ),
            )
            db.execute(
                "UPDATE memories SET accesses=?,accessed=? WHERE bank=? AND id=?",
                (
                    int(metadata.get("access_count", 0) or 0),
                    float(metadata.get("last_access_time", 0) or 0),
                    bank,
                    key,
                ),
            )
            db.execute(
                "INSERT INTO imported VALUES(?,?,?,?)",
                (bank, source, str(source_id), digest),
            )
        return True

    def seal_migration(
        self, bank: str, host: dict, title="迁移基线：保留旧记忆", summary_cursor=None
    ):
        """Historical stores lack full snapshots: seal them as one honest baseline."""
        with self.transaction() as db:
            self._writable(db, bank)
            if db.execute("SELECT 1 FROM nodes WHERE bank=?", (bank,)).fetchone():
                raise ValueError("只有尚无总结节点的新空间可以建立迁移基线")
            retained_end = (
                db.execute(
                    "SELECT MAX(id) FROM messages WHERE bank=?", (bank,)
                ).fetchone()[0]
                or 0
            )
            cursor = retained_end if summary_cursor is None else summary_cursor
            node = db.execute(
                "INSERT INTO nodes(bank,cursor,title,created,snapshot,host,source_start,trigger) VALUES(?,?,?,?,?,?,0,'migration')",
                (bank, cursor, title, time.time(), "{}", dumps(host)),
            ).lastrowid
            db.execute(
                "UPDATE memories SET node=? WHERE bank=? AND node IS NULL", (node, bank)
            )
            snapshot = self._snapshot(db, bank)
            snapshot["retained_end"] = retained_end
            db.execute(
                "UPDATE nodes SET snapshot=? WHERE id=?",
                (pack_snapshot(snapshot), node),
            )
            db.execute(
                "UPDATE banks SET cursor=?,revision=revision+1 WHERE id=?",
                (cursor, bank),
            )
            return node
