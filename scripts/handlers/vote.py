"""Vote handler (投票 / 投A / 投票結果 / 取消投票)."""

import re
from collections import Counter
from datetime import datetime
from state import vote_get, vote_set, vote_delete
from goal_tracker import TW_TZ
from config import MEMBERS


def handle_vote(text: str, group_id: str, member_label: str) -> str | None:
    """Handle vote commands. Returns reply_text or None."""
    m = re.match(r'^投票\s+(.+?)(?:\s{1,2}|\s*[,，]\s*)(.+)$', text)
    if m:
        gid = group_id or "default"
        question = m.group(1).strip()
        raw_opts = re.split(r'[\s,，]+', m.group(2).strip())
        opts = [o for o in raw_opts if o][:4]
        if len(opts) >= 2:
            vote_set(gid, {
                "question": question,
                "options": opts,
                "votes": {},
                "ts": datetime.now(TW_TZ).isoformat(),
            })
            letters = ["A", "B", "C", "D"]
            opts_str = "\n".join(f"  {letters[i]}. {opts[i]}" for i in range(len(opts)))
            return (
                f"📊 投票開始！\n\n{question}\n\n{opts_str}\n\n"
                f"傳「投A」「投B」... 投票，傳「投票結果」查看"
            )
        else:
            return "格式：投票 問題 選項1 選項2 選項3\n例：投票 週末去哪 北部 中部 南部"

    m = re.match(r'^投([ABCD])$', text.strip().upper())
    if m:
        gid = group_id or "default"
        vstate = vote_get(gid)
        if vstate:
            chosen = m.group(1)
            idx = ord(chosen) - ord("A")
            if idx < len(vstate["options"]):
                vstate["votes"][member_label] = chosen
                opt_name = vstate["options"][idx]
                reply_text = f"✅ {member_label} 投了 {chosen}. {opt_name}"
                if len(vstate["votes"]) >= max(2, len(MEMBERS)):
                    cnt = Counter(vstate["votes"].values())
                    winner_letter = cnt.most_common(1)[0][0]
                    winner_idx = ord(winner_letter) - ord("A")
                    winner_name = vstate["options"][winner_idx]
                    detail = " / ".join(f"{k}: {v}票" for k, v in sorted(cnt.items()))
                    reply_text += f"\n\n🎉 全員投票完畢！\n結果：{winner_letter}. {winner_name} 勝出\n（{detail}）"
                    vote_delete(gid)
                return reply_text

    if text == "投票結果":
        gid = group_id or "default"
        vstate = vote_get(gid)
        if vstate:
            cnt = Counter(vstate["votes"].values())
            lines = [f"📊 {vstate['question']} 目前票數："]
            letters = ["A", "B", "C", "D"]
            for i, opt in enumerate(vstate["options"]):
                letter = letters[i]
                votes = cnt.get(letter, 0)
                bar = "█" * votes + "░" * (len(MEMBERS) - votes)
                who = [n for n, v in vstate["votes"].items() if v == letter]
                lines.append(f"  {letter}. {opt}｜{bar} {votes}票 {('（' + '、'.join(who) + '）') if who else ''}")
            return "\n".join(lines)
        else:
            return "目前沒有進行中的投票"

    if text == "取消投票":
        gid = group_id or "default"
        vote_delete(gid)
        return "投票已取消"

    return None
