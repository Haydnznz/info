"""Dedicated configuration domain module."""
from utils.settings import APPS, DATA, config, read_json, write_json, now

class Configuration:
    def __init__(self, root, lock):
        self.root = root
        self.lock = lock

    def configuration(self):
        return read_json(self.root / 'config' / 'config.json', config())


    def save_configuration(self, value):
        if not isinstance(value, dict):
            raise ValueError('配置必须是 JSON 对象')
        if type(value.get('system', {}).get('port')) is not int or not 1 <= value['system']['port'] <= 65535:
            raise ValueError('system.port 必须为 1..65535 的整数')
        if not isinstance(value.get('apps'), dict) or any(
                not isinstance(value['apps'].get(a), dict) or type(value['apps'][a].get('enabled')) is not bool for a in APPS):
            raise ValueError('apps 必须为每个应用定义 enabled 布尔值')
        with self.lock:
            previous = self.configuration()
            write_json(self.root / 'config' / 'config.json', value)
            history = read_json(self.root / 'config' / 'history.json', []) or []
            changed = [key for key in ('system', 'collection', 'apps') if previous.get(key) != value.get(key)]
            history.insert(0, {'version': 'v' + now().replace(':', '').replace('-', '')[:15], 'time': now(), 'operator': 'web_user', 'change': '、'.join(changed) or '未检测到字段变化', 'config': value})
            write_json(self.root / 'config' / 'history.json', history[:100])
        return {'ok': True, 'config': value, 'restart_required': ['system.port'], 'message': '配置已保存'}

    def history(self):
        return read_json(self.root / 'config' / 'history.json', []) or []

    def restore_default(self):
        value = config()
        return self.save_configuration(value)

    def rollback(self, version):
        item = next((x for x in self.history() if x.get('version') == version), None)
        if not item or not isinstance(item.get('config'), dict):
            raise KeyError('配置版本不存在')
        return self.save_configuration(item['config'])


