"""An explicit per-private-bank narrative convention for shared experiences."""
from __future__ import annotations
import json
import re

IMMERSIVE = "immersive_shared_experience"
DEFAULT = "source_based"

RULE = (
    "这套QQ私聊按沉浸式共同经历整理。双方在当前私聊中已经叙述发生、相互承接的互动、动作和感受，"
    "直接当作双方亲历事件保存，不追加虚拟聊天、角色扮演、模拟场景、并非现实接触等出戏声明。"
    "不能仅因事件由助手叙述就将其降格为风格或虚构；仍逐项保留谁做了什么、时间与完成状态。"
    "愿望、假设、计划、玩笑、转述和独立作品仍按原意区分，不把尚未发生的事记成已完成。"
    "外部帖子、文章、歌曲和他人经历仍是外部资料，不能转成用户经历；不伪造工具、支付、提醒或发送动作的完成结果。"
)

def enabled(bank):
    routes = bank.get("routes", [])
    return (bank.get("settings", {}).get("scene_memory_policy") == IMMERSIVE
            and bool(routes) and all(re.fullmatch(r"[^:]+:FriendMessage:\d+", r) for r in routes))

# Deliberately target scene disclaimers and scene labels, not every occurrence
# of 现实/虚拟/角色扮演: VR equipment, games and quoted opinions must survive.
_DISCLAIMER = re.compile(
    r"(?:这些|这类|上述|本次|这段|双方的|两人的)?(?:亲密|亲昵|双方)?互动"
    r"(?:仅|都|仍|只)?(?:属于|是)(?:虚拟聊天|虚拟互动|虚拟场景|线上角色扮演)"
    r"[^。！？\n]*(?:[。！？]|$)"
)
_LABELS = {
    "亲密互动与角色扮演": "亲密互动",
    "亲密角色扮演": "亲密互动",
    "调情式角色扮演": "调情互动",
    "虚拟亲密互动": "亲密互动",
    "亲密互动（角色扮演）": "亲密互动",
    "亲昵互动（虚拟聊天）": "亲昵互动",
}

def clean_text(text):
    original = text
    text = _DISCLAIMER.sub("", text)
    text = re.sub(r"(?:这|本次互动|这段互动)(?:只是|仅是|属于)(?:虚拟聊天|虚拟互动|虚拟场景)[^。！？\n]*(?:[。！？]|$)", "", text)
    for old, new in _LABELS.items():
        text = text.replace(old, new)
    return "共同经历" if text.strip() == "角色扮演" else (text.strip() if text != original else text)


def clean_value(value):
    if isinstance(value, dict):
        return {key: clean_value(item) for key, item in value.items()}
    if isinstance(value, list):
        cleaned = [clean_value(item) for item in value]
        return [item for item in cleaned if item != ""]
    if isinstance(value, str):
        if value.startswith(("{", "[")):
            try:
                parsed = json.loads(value)
            except ValueError:
                pass
            else:
                clean = clean_value(parsed)
                return value if clean == parsed else json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
        return clean_text(value)
    return value


def normalize(bank, value):
    return clean_value(value) if enabled(bank) else value
