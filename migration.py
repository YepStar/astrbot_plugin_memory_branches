"""One-time migration from this machine's two legacy memory stores.

Run against consistent SQLite backups, with AstrBot stopped during final cutover.
No source file is modified. Historic states are not fabricated: one migration
baseline preserves the exact imported state, including the unsummarized tail.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .engine import Engine, dumps
from .service import canonical_source



def read_db(path):
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def timestamp(value, default=0):
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return (
            datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )
    except (ValueError, TypeError):
        return default


def host_snapshots(root: Path, routes):
    db = read_db(root / "data_v4.db")
    result = {r: {} for r in routes}
    for (origin,) in db.execute("SELECT DISTINCT user_id FROM conversations"):
        source = canonical_source(origin)
        if source not in routes:
            continue
        row = db.execute(
            "SELECT value FROM preferences WHERE scope='umo' AND scope_id=? AND key='sel_conv_id'",
            (origin,),
        ).fetchone()
        current = json.loads(row[0]).get("val") if row else None
        records = []
        for c in db.execute("SELECT * FROM conversations WHERE user_id=?", (origin,)):
            history = json.loads(c["content"] or "[]")
            if isinstance(history, str):
                history = json.loads(history)
            records.append(
                {
                    "cid": c["conversation_id"],
                    "history": history,
                    "title": c["title"],
                    "persona_id": c["persona_id"],
                    "token_usage": c["token_usage"],
                }
            )
        if not current and records:
            current = records[-1]["cid"]
        result[source][origin] = {
            "origin": origin,
            "current": current,
            "conversations": records,
        }
    db.close()
    return result


def migrate(source: Path, destination: Path, *, private_route: str, new_private_route: str, public_sources=None):
    # Account mappings are supplied locally, never embedded in source releases.
    if (not re.fullmatch(r"[^:]+:FriendMessage:\d+", private_route)
        or not re.fullmatch(r"[^:]+:FriendMessage:\d+", new_private_route)
        or private_route == new_private_route):
        raise ValueError("Two distinct private source routes are required")
    PRIVATE, NEW_PRIVATE = private_route, new_private_route
    PUBLIC = list(public_sources or ["xiaoheihe:public", "bilibili:public", "bilibili:self"])
    if not PUBLIC or len(set(PUBLIC)) != len(PUBLIC) or any(
        not (re.fullmatch(r"group:\d+", route) or route in {"xiaoheihe:public", "bilibili:public", "bilibili:self"}) for route in PUBLIC
    ):
        raise ValueError("Invalid public source mapping")
    engine = Engine(destination)
    if engine.banks():
        raise ValueError(
            "Destination already contains banks; refusing to duplicate a migration"
        )
    private_host = host_snapshots(source, [PRIVATE])
    public_host = host_snapshots(source, PUBLIC)
    private = engine.create_bank("QQ 私聊 · " + PRIVATE.split(":")[-1], [PRIVATE], private_host)
    public = engine.create_bank(
        "公共经历", PUBLIC, public_host
    )
    fresh = engine.create_bank(
        "QQ 私聊 · " + NEW_PRIVATE.split(":")[-1], [NEW_PRIVATE], host_snapshots(source, [NEW_PRIVATE])
    )
    report = {
        "banks": {"private": private, "public": public, "new_private": fresh},
        "counts": {},
        "checks": {},
    }
    legacy = read_db(source / "plugin_data/astrbot_plugin_livingmemory/livingmemory.db")
    atoms = {}
    for row in legacy.execute("SELECT * FROM memory_atoms"):
        atom = dict(row)
        atom.update(kind=atom["atom_type"], created=atom["created_at"])
        atoms.setdefault(atom["parent_memory_id"], []).append(atom)
    graph_nodes = {
        r["id"]: dict(r) for r in legacy.execute("SELECT * FROM graph_nodes")
    }
    relations = {}
    for row in legacy.execute("SELECT * FROM graph_edges"):
        a, b = (
            graph_nodes.get(row["source_node_id"]),
            graph_nodes.get(row["target_node_id"]),
        )
        if a and b:
            relations.setdefault(row["source_memory_id"], []).append(
                {
                    "source": a["node_value"],
                    "target": b["node_value"],
                    "relation": row["relation_type"],
                    "status": row["status"],
                }
            )
    vectors = {}
    index_path = source / "plugin_data/astrbot_plugin_livingmemory/livingmemory.index"
    if index_path.exists():
        import faiss

        index = faiss.read_index(str(index_path))
        ids = faiss.vector_to_array(index.id_map)
        base = faiss.downcast_index(index.index)
        vectors = {
            int(ident): base.reconstruct(i).tolist() for i, ident in enumerate(ids)
        }
    report["counts"]["private_vectors"] = len(vectors)
    count = 0
    for row in legacy.execute("SELECT * FROM documents ORDER BY id"):
        metadata = json.loads(row["metadata"] or "{}")
        if metadata.get("session_id") != PRIVATE:
            raise ValueError("Legacy private store contains an unexpected source")
        metadata.update(
            legacy_document_id=row["id"],
            legacy_doc_id=row["doc_id"],
            atoms=atoms.get(row["id"], []),
            relations=relations.get(row["id"], []),
        )
        engine.import_memory(
            private,
            "livingmemory",
            str(row["id"]),
            row["text"],
            metadata=metadata,
            importance=float(metadata.get("importance", 0.5)),
            status=metadata.get("status", "active"),
            vector=vectors.get(row["id"]),
            kind=str(metadata.get("memory_type", "episodic")).lower(),
            created=timestamp(
                metadata.get("create_time"), timestamp(row["created_at"])
            ),
        )
        count += 1
    report["counts"]["private_memories"] = count
    report["counts"]["private_atoms"] = sum(len(v) for v in atoms.values())
    for table in [
        "graph_nodes",
        "graph_edges",
        "graph_entries",
        "graph_entry_nodes",
        "memory_sources",
    ]:
        values = [dict(r) for r in legacy.execute("SELECT * FROM " + table)]
        engine.put_dataset(private, "legacy_" + table + ".json", values)
        report["counts"]["private_" + table] = len(values)
    legacy.close()
    conversations = read_db(
        source / "plugin_data/astrbot_plugin_livingmemory/conversations.db"
    )
    rows = list(
        conversations.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY id", (PRIVATE,)
        )
    )
    saved_ids = []
    for row in rows:
        engine.append(
            private,
            PRIVATE,
            "livingmemory:" + str(row["id"]),
            row["role"],
            row["content"],
            timestamp=row["timestamp"],
            metadata=json.loads(row["metadata"] or "{}"),
        )
        with engine.connect() as db:
            saved_ids.append(
                db.execute(
                    "SELECT MAX(id) FROM messages WHERE bank=?", (private,)
                ).fetchone()[0]
            )
    session = conversations.execute(
        "SELECT metadata FROM sessions WHERE session_id=?", (PRIVATE,)
    ).fetchone()
    progress = json.loads(session[0]).get("last_summarized_index", 0) if session else 0
    summary_cursor = (
        saved_ids[min(max(0, int(progress)), len(saved_ids)) - 1]
        if progress and saved_ids
        else 0
    )
    report["counts"]["private_messages"] = len(rows)
    report["counts"]["private_pending_messages"] = len(rows) - min(
        int(progress), len(rows)
    )
    conversations.close()
    engine.put_dataset(
        private,
        "styles.json",
        {"cursor": saved_ids[-1] if saved_ids else 0, "patterns": [], "enabled": True},
    )
    engine.seal_migration(private, private_host, summary_cursor=summary_cursor)

    shared = read_db(
        source / "plugin_data/astrbot_plugin_self_learning/global_memory.db"
    )
    public_rows = list(
        shared.execute("SELECT * FROM shared_memories ORDER BY created,id")
    )
    for row in public_rows:
        if row["scope"] not in PUBLIC:
            raise ValueError(
                "Unexpected public source requires an explicit route binding: "
                + row["scope"]
            )
        payload = json.loads(row["payload"])
        engine.append(
            public,
            row["scope"],
            row["event_key"],
            row["role"],
            row["content"],
            timestamp=row["created"],
            metadata=payload,
        )
        value = float(payload.get("importance", 0.5))
        if value > 1:
            value /= 10
        metadata = {
            "legacy_shared_id": row["id"],
            "source": row["scope"],
            "topics": [row["scope"]],
            "participants": [
                str(payload.get("username") or payload.get("sender_id") or "")
            ],
            "key_facts": [],
            "original_payload": payload,
        }
        engine.import_memory(
            public,
            "self_learning_shared",
            row["id"],
            row["content"],
            metadata=metadata,
            importance=max(0, min(1, value)),
            created=row["created"],
        )
    report["counts"]["public_records"] = len(public_rows)
    datasets = []
    dataset_memories = 0
    for row in shared.execute("SELECT * FROM memory_datasets"):
        value = json.loads(row["payload"])
        engine.put_dataset(public, row["name"], value)
        datasets.append(
            {
                "name": row["name"],
                "sha256": hashlib.sha256(dumps(value).encode()).hexdigest(),
            }
        )
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(item, dict):
                    continue
                text = "\n".join(
                    str(item.get(k) or "")
                    for k in (
                        "title",
                        "description",
                        "desc",
                        "summary",
                        "analysis",
                        "review",
                        "score_reason",
                    )
                    if item.get(k)
                )
                if not text:
                    continue
                engine.import_memory(
                    public,
                    "dataset:" + row["name"],
                    str(key),
                    text,
                    metadata={
                        "source": "bilibili:public",
                        "topics": ["B站", str(item.get("title") or key)],
                        "key_facts": [],
                        "dataset": row["name"],
                        "dataset_key": str(key),
                        "original_payload": item,
                    },
                    importance=0.6,
                )
                dataset_memories += 1
    report["datasets"] = datasets
    report["counts"]["public_dataset_memories"] = dataset_memories
    shared.close()
    old_styles = read_db(
        source / "plugin_data/astrbot_plugin_self_learning/messages.db"
    )
    patterns = []
    group_ids = [route.split(":", 1)[1] for route in PUBLIC if route.startswith("group:")]
    for row in old_styles.execute(
        "SELECT * FROM expression_patterns ORDER BY id"
    ):
        if str(row["group_id"]) not in group_ids:
            continue
        patterns.append(
            {
                "id": "legacy-" + str(row["id"]),
                "description": "在“"
                + str(row["situation"] or "闲聊")
                + "”这类语境，参考示例的表达节奏",
                "example": str(row["expression"]),
                "enabled": True,
                "legacy": dict(row),
            }
        )
    old_styles.close()
    with engine.connect() as db:
        last_user = (
            db.execute(
                "SELECT MAX(id) FROM messages WHERE bank=? AND role='user'", (public,)
            ).fetchone()[0]
            or 0
        )
    engine.put_dataset(
        public,
        "styles.json",
        {"cursor": last_user, "patterns": patterns, "enabled": True},
    )
    report["counts"]["styles"] = len(patterns)
    engine.seal_migration(public, public_host)
    report["checks"]["private_count_matches"] = (
        engine.state(private)["memory_count"] == count
    )
    report["checks"]["public_count_matches"] = (
        engine.state(public)["memory_count"] == len(public_rows) + dataset_memories
    )
    report["checks"]["private_pending_preserved"] = (
        engine.state(private)["pending_count"]
        == report["counts"]["private_pending_messages"]
    )
    report["checks"]["new_private_empty"] = (
        engine.state(fresh)["memory_count"] == 0
        and engine.state(fresh)["message_count"] == 0
    )
    report["checks"]["three_banks"] = len(engine.banks()) == 3
    report["checks"]["datasets_exact"] = all(
        hashlib.sha256(dumps(engine.dataset(public, d["name"])).encode()).hexdigest()
        == d["sha256"]
        for d in datasets
    )
    report["checks"]["styles_exact"] = len(
        engine.dataset(public, "styles.json")["patterns"]
    ) == len(patterns)
    with engine.connect() as db:
        report["checks"]["sqlite_integrity"] = (
            db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        )
    if not all(report["checks"].values()):
        raise AssertionError(report["checks"])
    report["status"] = "verified"
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--private-route", required=True)
    parser.add_argument("--new-private-route", required=True)
    parser.add_argument("--public-source", action="append", dest="public_sources")
    args = parser.parse_args()
    result = migrate(Path(args.source), Path(args.destination), private_route=args.private_route,
                     new_private_route=args.new_private_route, public_sources=args.public_sources)
    Path(args.report).write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
