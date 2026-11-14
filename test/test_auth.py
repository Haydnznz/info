import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class AuthRoleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = {key: os.environ.get(key) for key in (
            'AUTH_ENABLED', 'AUTH_SECRET', 'AUTH_ADMIN_PASSWORD', 'AUTH_OPERATOR_PASSWORD',
            'AUTH_VIEWER_PASSWORD', 'OPINION_DATA_DIR', 'MONGO_ENABLED', 'LLM_ENABLED')}
        os.environ.update({
            'AUTH_ENABLED': 'true',
            'AUTH_SECRET': 'test-auth-secret',
            'AUTH_ADMIN_PASSWORD': 'admin-pass',
            'AUTH_OPERATOR_PASSWORD': 'operator-pass',
            'AUTH_VIEWER_PASSWORD': 'viewer-pass',
            'OPINION_DATA_DIR': self.tmp.name,
            'MONGO_ENABLED': 'false',
            'LLM_ENABLED': 'false',
        })
        from web import fastapi_app
        from web.services import Service
        fastapi_app.service = Service(Path(self.tmp.name))
        self.client = TestClient(fastapi_app.app)

    def tearDown(self):
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def login(self, username, password):
        response = self.client.post('/api/auth/login', json={'username': username, 'password': password})
        self.assertEqual(200, response.status_code, response.text)
        return response.json()['access_token']

    def test_login_and_role_boundaries(self):
        self.assertEqual(401, self.client.get('/api/status').status_code)
        operator = self.login('operator', 'operator-pass')
        viewer = self.login('viewer', 'viewer-pass')
        admin = self.login('admin', 'admin-pass')

        self.assertEqual(200, self.client.get('/api/status', headers={'Authorization': 'Bearer ' + viewer}).status_code)
        self.assertEqual(200, self.client.post('/api/system/start', headers={'Authorization': 'Bearer ' + admin}, json={}).status_code)
        self.assertEqual(202, self.client.post('/api/search', headers={'Authorization': 'Bearer ' + operator},
                                                json={'query': '权限测试', 'source_types': ['internal_data'], 'async': True}).status_code)
        self.assertEqual(403, self.client.post('/api/search', headers={'Authorization': 'Bearer ' + viewer},
                                                json={'query': '越权测试', 'source_types': ['internal_data'], 'async': True}).status_code)
        self.assertEqual(403, self.client.get('/api/config', headers={'Authorization': 'Bearer ' + viewer}).status_code)
        self.assertEqual(200, self.client.get('/api/config', headers={'Authorization': 'Bearer ' + admin}).status_code)

    def test_invalid_login_and_explicit_bearer(self):
        bad = self.client.post('/api/auth/login', json={'username': 'admin', 'password': 'wrong'})
        self.assertEqual(401, bad.status_code)
        me = self.client.get('/api/auth/me', headers={'Authorization': 'Bearer ' + self.login('admin', 'admin-pass')})
        self.assertEqual('admin', me.json()['user']['username'])


if __name__ == '__main__':
    unittest.main()
