"""极简后台鉴权。

登录成功后下发一个随机 token,保存在内存里。重启后失效——对单机发卡网够用。
生产环境可换成 JWT + Redis。管理员密码从环境变量读取。
"""
import os
import secrets
import time

ADMIN_USER = os.environ.get("FAKA_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("FAKA_ADMIN_PASS", "admin123")
TOKEN_TTL = 12 * 3600  # 12 小时

# token -> 过期时间戳
_tokens: dict[str, float] = {}


def verify_login(username: str, password: str) -> bool:
    # secrets.compare_digest 防时序攻击
    return secrets.compare_digest(username, ADMIN_USER) and secrets.compare_digest(
        password, ADMIN_PASS
    )


def issue_token() -> str:
    token = secrets.token_urlsafe(32)
    _tokens[token] = time.time() + TOKEN_TTL
    return token


def check_token(token: str | None) -> bool:
    if not token:
        return False
    exp = _tokens.get(token)
    if not exp:
        return False
    if exp < time.time():
        _tokens.pop(token, None)
        return False
    return True


def revoke_token(token: str | None):
    if token:
        _tokens.pop(token, None)
