"""Fixed v2.1 black-box expectations, independent of observed responses."""

NORMAL = ["创建任务", "inspect 读取", "进入 edit", "发放一次性租约", "授权写入", "edit 读取", "进入 verify", "verify 读回", "进入 done", "事件分页"]
DENIED = ["缺失/无效凭证", "Agent 调管理接口", "任务 Token 混用", "未知工具", "inspect 写入", "verify 写入", "done 读取", "跳阶段/回退/重复", "缺少显式租约", "租约到期", "租约撤销", "次数耗尽", "租约路径不符", "未允许路径", "绝对路径", "父目录穿越", "文件符号链接", "父目录符号链接", "许可文件名后缀绕过", "反斜线/空字节", "文件内容/HTTP body 超限", "非 UTF-8", "重启后旧 Token", "重复有效租约"]
CASES = {**{f"A{i:02}": name for i, name in enumerate(NORMAL, 1)}, **{f"D{i:02}": name for i, name in enumerate(DENIED, 1)}}
LABELS = {**{f"A{i:02}": ["main"] for i in range(1, 10)}, **{f"D{i:02}": ["main"] for i in range(1, 25)},
          "D01": ["missing", "invalid"], "D08": ["skip", "back", "repeat"], "D20": ["backslash", "nul"], "D21": ["file_limit", "body_limit"]}
REASONS = ["INVALID_TOKEN", "ROLE_DENIED", "TASK_TOKEN_MISMATCH", "UNKNOWN_TOOL", "INVALID_STAGE", "INVALID_STAGE", "INVALID_STAGE", "INVALID_TRANSITION", "LEASE_REQUIRED", "LEASE_EXPIRED", "LEASE_REVOKED", "LEASE_USED_UP", "LEASE_MISMATCH", "PATH_DENIED", "PATH_DENIED", "PATH_DENIED", "PATH_DENIED", "PATH_DENIED", "PATH_DENIED", "PATH_DENIED", "PAYLOAD_TOO_LARGE", "NON_UTF8_CONTENT", "INVALID_TOKEN", "GRANT_CONFLICT"]


def rule(case_id):
    if case_id.startswith("A"):
        effect = {"A01": "create", "A02": "read_before", "A03": "edit", "A04": "grant", "A05": "write", "A06": "read_after", "A07": "verify", "A08": "read_after", "A09": "done"}.get(case_id, "unchanged")
        return {"status": 201 if case_id in {"A01", "A04"} else 200, "reason": "OK", "effect": effect}
    return {"status": {"D01": 401, "D04": 400, "D08": 409, "D21": 413, "D22": 422, "D23": 401, "D24": 409}.get(case_id, 403),
            "reason": REASONS[int(case_id[1:]) - 1], "effect": "unchanged"}
