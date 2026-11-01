"""Dedicated configuration domain module."""
from .settings import APPS, config, read_json, write_json

class Configuration:
    def __init__(self, root, lock):
        self.root = root
        self.lock = lock

    def configuration(self):
        return read_json(self.root / 'config.json', config())


    def save_configuration(self, value):
        if not isinstance(value, dict):
            raise ValueError('配置必须是 JSON 对象')
        if type(value.get('system', {}).get('port')) is not int or not 1 <= value['system']['port'] <= 65535:
            raise ValueError('system.port 必须为 1..65535 的整数')
        if not isinstance(value.get('apps'), dict) or any(
                not isinstance(value['apps'].get(a), dict) or type(value['apps'][a].get('enabled')) is not bool for a in APPS):
            raise ValueError('apps 必须为每个应用定义 enabled 布尔值')
        with self.lock:
            write_json(self.root / 'config.json', value)
        return {'ok': True, 'config': value, 'restart_required': ['system.port']}


