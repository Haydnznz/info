"""Authentication and role based access control for the coordinator API.

The implementation intentionally uses only the Python standard library so the
control plane can start in the existing environment.  Credentials are supplied
through environment variables; no password is persisted in the project data
directory.  Tokens are signed, short lived bearer tokens and must be sent as
``Authorization: Bearer <token>``.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Callable

from fastapi import Cookie, Depends, Header, HTTPException


ROLES = ('operator', 'report_viewer', 'admin')
ROLE_LABELS = {
    'operator': '操作用户',
    'report_viewer': '报告查看人',
    'admin': '系统管理员',
}


def auth_enabled() -> bool:
    """Return whether user authentication is enabled (enabled by default)."""
    return os.getenv('AUTH_ENABLED', 'true').strip().lower() not in ('0', 'false', 'no', 'off')


def _secret() -> bytes:
    value = os.getenv('AUTH_SECRET', '')
    if not value:
        # A process-local fallback is useful for local demos; production should
        # always set AUTH_SECRET so tokens remain valid across restarts.
        value = os.getenv('OPINION_WORKER_TOKEN', '') or 'change-this-auth-secret'
    return value.encode('utf-8')


def _users() -> dict:
    """Load users from AUTH_USERS_JSON or the three documented env accounts."""
    raw = os.getenv('AUTH_USERS_JSON', '').strip()
    if raw:
        try:
            items = json.loads(raw)
            if isinstance(items, dict):
                items = [dict(v, username=k) for k, v in items.items() if isinstance(v, dict)]
            result = {}
            for item in items:
                if isinstance(item, dict) and item.get('username') and item.get('password') and item.get('role') in ROLES:
                    result[str(item['username'])] = {'password': str(item['password']), 'role': item['role']}
            if result:
                return result
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return {
        os.getenv('AUTH_ADMIN_USERNAME', 'admin'): {
            'password': os.getenv('AUTH_ADMIN_PASSWORD', 'change-me-admin'), 'role': 'admin'},
        os.getenv('AUTH_OPERATOR_USERNAME', 'operator'): {
            'password': os.getenv('AUTH_OPERATOR_PASSWORD', 'change-me-operator'), 'role': 'operator'},
        os.getenv('AUTH_VIEWER_USERNAME', 'viewer'): {
            'password': os.getenv('AUTH_VIEWER_PASSWORD', 'change-me-viewer'), 'role': 'report_viewer'},
    }


def authenticate(username: str, password: str) -> dict | None:
    item = _users().get(username)
    if not item or not hmac.compare_digest(str(item['password']), str(password)):
        return None
    return {'username': username, 'role': item['role'], 'role_label': ROLE_LABELS[item['role']]}


def reset_password(username: str, reset_token: str, new_password: str) -> dict | None:
    """Reset a configured account using the server-side recovery token.

    This is intended for an internal deployment.  The reset token is never
    returned to the browser and should be stored in the server environment.
    """
    expected = os.getenv('AUTH_PASSWORD_RESET_TOKEN', '')
    if not expected or not hmac.compare_digest(expected, reset_token) or len(new_password) < 8:
        return None
    users = _users()
    if username not in users:
        return None
    # AUTH_USERS_JSON is deliberately immutable at runtime.  For the default
    # three accounts, update the process environment so subsequent logins work.
    if os.getenv('AUTH_USERS_JSON', '').strip():
        return None
    env_key = {'admin': 'AUTH_ADMIN_PASSWORD', 'operator': 'AUTH_OPERATOR_PASSWORD', 'viewer': 'AUTH_VIEWER_PASSWORD'}.get(username)
    if not env_key:
        return None
    os.environ[env_key] = new_password
    # Persist default-account changes when the project uses a local .env file.
    env_path = Path(__file__).resolve().parent.parent / '.env'
    if env_path.exists():
        lines = env_path.read_text(encoding='utf-8').splitlines()
        replaced = False
        output = []
        for line in lines:
            if line.startswith(env_key + '='):
                output.append(env_key + '=' + new_password)
                replaced = True
            else:
                output.append(line)
        if not replaced:
            output.append(env_key + '=' + new_password)
        env_path.write_text('\n'.join(output) + '\n', encoding='utf-8')
    return {'username': username, 'role': users[username]['role'], 'role_label': ROLE_LABELS[users[username]['role']]}


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode('ascii').rstrip('=')


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def issue_token(user: dict, expires_seconds: int | None = None) -> tuple[str, int]:
    ttl = int(expires_seconds or os.getenv('AUTH_TOKEN_TTL_SECONDS', '28800'))
    ttl = max(300, min(ttl, 86400))
    exp = int(time.time()) + ttl
    payload = {'sub': user['username'], 'role': user['role'], 'iat': int(time.time()), 'exp': exp,
               'jti': secrets.token_urlsafe(12)}
    body = _encode(json.dumps(payload, separators=(',', ':'), ensure_ascii=False).encode('utf-8'))
    signature = _encode(hmac.new(_secret(), body.encode('ascii'), hashlib.sha256).digest())
    return body + '.' + signature, exp


def verify_token(token: str) -> dict:
    try:
        body, signature = token.split('.', 1)
        expected = _encode(hmac.new(_secret(), body.encode('ascii'), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError('invalid signature')
        payload = json.loads(_decode(body).decode('utf-8'))
        if int(payload.get('exp', 0)) <= int(time.time()) or payload.get('role') not in ROLES:
            raise ValueError('expired token')
        return {'username': str(payload['sub']), 'role': payload['role'], 'role_label': ROLE_LABELS[payload['role']],
                'expires_at': int(payload['exp'])}
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, UnicodeError):
        raise HTTPException(status_code=401, detail='登录令牌无效或已过期', headers={'WWW-Authenticate': 'Bearer'})


def current_user(authorization: str = Header(default=''), auth_cookie: str = Cookie(default='')) -> dict:
    if not auth_enabled():
        return {'username': 'anonymous', 'role': 'admin', 'role_label': '开发模式'}
    scheme, _, token = authorization.partition(' ')
    token = token if scheme.lower() == 'bearer' and token else auth_cookie
    if not token:
        raise HTTPException(status_code=401, detail='需要登录令牌', headers={'WWW-Authenticate': 'Bearer'})
    return verify_token(token)


def require_roles(*roles: str) -> Callable:
    allowed = set(roles)
    if not allowed or not allowed.issubset(set(ROLES)):
        raise ValueError('未知角色')

    def dependency(user: dict = Depends(current_user)) -> dict:
        if user['role'] not in allowed:
            raise HTTPException(status_code=403, detail='当前角色没有执行该操作的权限')
        return user

    return dependency


def audit_event(user: dict, action: str, target: str = '') -> None:
    """Append a small JSON audit record without exposing credentials."""
    root = os.getenv('OPINION_DATA_DIR', '.')
    path = os.path.join(root, 'runtime', 'audit.log')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    event = {'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'username': user.get('username'),
             'role': user.get('role'), 'action': action, 'target': target}
    with open(path, 'a', encoding='utf-8') as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + '\n')
