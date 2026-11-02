"""Compatible launcher. Run --help for distributed roles."""
import argparse
import os
import secrets
import subprocess
import sys
import time
from utils.settings import config


def main():
    parser = argparse.ArgumentParser(description='Distributed opinion analysis platform')
    parser.add_argument('role', nargs='?', default='all', choices=['all', 'api', 'worker'])
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=config().get('system', {}).get('port', 9000))
    parser.add_argument('--coordinator', default='http://127.0.0.1:9000')
    parser.add_argument('--worker-id')
    parser.add_argument('--workers', type=int, default=2)
    args = parser.parse_args()
    if args.role == 'api':
        if not os.environ.get('OPINION_WORKER_TOKEN'):
            parser.error('Set OPINION_WORKER_TOKEN for the API role')
        from web.api import serve
        serve(args.host, args.port)
    elif args.role == 'worker':
        from tool.worker import Worker
        Worker(args.coordinator, args.worker_id).run()
    else:
        if not 1 <= args.workers <= 64:
            parser.error('--workers must be in 1..64')
        env = dict(os.environ)
        env.setdefault('OPINION_WORKER_TOKEN', secrets.token_urlsafe(32))
        children = []
        base = [sys.executable, '-u', os.path.abspath(__file__)]
        target = '127.0.0.1' if args.host == '0.0.0.0' else args.host
        try:
            children.append(subprocess.Popen(base + ['api', '--host', args.host, '--port', str(args.port)], env=env))
            for _ in range(args.workers):
                children.append(subprocess.Popen(base + ['worker', '--coordinator', f'http://{target}:{args.port}'], env=env))
            while all(c.poll() is None for c in children):
                time.sleep(.5)
            raise SystemExit('Child process exited; local cluster stopped. Check logs above.')
        except KeyboardInterrupt:
            pass
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
            for child in children:
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


if __name__ == '__main__':
    main()
