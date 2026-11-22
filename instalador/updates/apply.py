#!/usr/bin/python3
"""Install/reconcile the packaged corrections, preserving native PHP and credentials."""
import argparse
from datetime import datetime, timezone
import grp
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import shlex
import socket
import signal
import stat
import subprocess
import sys
import time
from xui_resources import budgets, effective_cpus, effective_memory

BASE = Path(__file__).resolve().parent
POST_SHA = '205eb0797e9aacaaa94d6681c0e91da51a60274c3e393de7eae987c70e33bd96'
BEGIN, END = '# BEGIN XUI STABILITY v2', '# END XUI STABILITY v2'


def run(args, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)


def managed(text, addition):
    text = re.sub(r'(?ms)^' + re.escape(BEGIN) + r'\n.*?^' + re.escape(END) + r'\n?', '', text)
    return text.rstrip() + '\n\n' + BEGIN + '\n' + addition.rstrip() + '\n' + END + '\n'


class Files:
    def __init__(self, root):
        self.root = root
        self.records = {}
        self.changed = []
        self.backup = self.path('/var/backups/xui-updates/' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))

    def path(self, name): return self.root / str(name).lstrip('/')

    def write(self, name, content, mode=None):
        path = self.path(name)
        # Preserve the distribution's my.cnf symlink and write its target.
        if path.is_symlink():
            target = os.readlink(path)
            path = self.path(target) if target.startswith('/') else path.parent / target
            while path.is_symlink():
                target = os.readlink(path)
                path = self.path(target) if target.startswith('/') else path.parent / target
        data = content.encode() if isinstance(content, str) else content
        if path.is_file() and path.read_bytes() == data:
            if mode is not None: path.chmod(mode)
            return False
        key = str(path.relative_to(self.root))
        if key not in self.records:
            self.backup.mkdir(parents=True, exist_ok=True); self.backup.chmod(0o700)
            if path.exists():
                info = path.stat(); saved = self.backup / 'files' / key
                saved.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(path, saved)
                self.records[key] = dict(existed=True, mode=stat.S_IMODE(info.st_mode), uid=info.st_uid, gid=info.st_gid)
            else: self.records[key] = dict(existed=False)
        info = path.stat() if path.exists() else None
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.xui-new')
        temporary.write_bytes(data)
        temporary.chmod(mode if mode is not None else (stat.S_IMODE(info.st_mode) if info else 0o644))
        if info and os.geteuid() == 0: os.chown(temporary, info.st_uid, info.st_gid)
        temporary.replace(path); self.changed.append(str(path))
        return True

    def save(self):
        if not self.records: return
        for key, record in self.records.items():
            target = self.path(key)
            record['installed_sha256'] = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
        self.backup.mkdir(parents=True, exist_ok=True); self.backup.chmod(0o700)
        (self.backup / 'manifest.json').write_text(json.dumps(self.records, indent=2))
        (self.backup / 'manifest.json').chmod(0o600)

    def rollback(self):
        for key, record in reversed(list(self.records.items())):
            path = self.path(key)
            if record['existed']:
                shutil.copy2(self.backup / 'files' / key, path)
                if os.geteuid() == 0: os.chown(path, record['uid'], record['gid'])
                path.chmod(record['mode'])
            elif path.exists(): path.unlink()


def set_option(text, name, value, separator=' = '):
    pattern = r'(?m)^\s*' + re.escape(name) + r'\s*=.*$'
    line = name + separator + str(value)
    return re.sub(pattern, line, text) if re.search(pattern, text) else text.rstrip() + '\n' + line + '\n'


def configure_mysql(files, profile, fresh):
    path = files.path('/etc/mysql/my.cnf')
    text = path.read_text() if path.exists() else ''
    addition = '[mysqld]\nmax_connections={}\ntmp_table_size=64M\nmax_heap_table_size=64M\nslow_query_log=ON\nslow_query_log_file=/var/log/mysql/mariadb-slow.log\nlong_query_time=2\n'.format(profile['mysql_connections'])
    if fresh:
        addition += 'innodb_buffer_pool_size={}M\nquery_cache_type=0\nquery_cache_size=0\nbind-address=127.0.0.1\nsql_mode=NO_ENGINE_SUBSTITUTION\n'.format(profile['innodb_mb'])
    elif BEGIN in text:
        for name in ['innodb_buffer_pool_size','query_cache_type','query_cache_size','bind-address','sql_mode']:
            old = re.search(r'(?m)^' + re.escape(name) + r'=(.+)$', text)
            if old: addition += name + '=' + old.group(1) + '\n'
    files.write('/etc/mysql/my.cnf', managed(text, addition))
    return addition


def queue_assets(files, profile):
    source = BASE / 'mass_queue'
    post = files.path('/home/xui/admin/post.php')
    if hashlib.sha256(post.read_bytes()).hexdigest() != POST_SHA: raise RuntimeError('Controlador PHP diferente do XUI 1.5.5 validado; fila não alterada.')
    state = '/var/lib/xui-mass-queue'
    previous = files.path('/home/xui/tmp/xui-mass-queue')
    marker = files.path(state + '/migration-complete.json')
    if previous.exists() and not marker.exists():
        if (previous / 'lease.json').exists(): raise RuntimeError('Existe lote reservado. Aguarde/cancele a fila antes de migrar o estado.')
        for folder in ['jobs','owners','dispatch']:
            for entry in (previous / folder).glob('*.json'):
                target = files.path(state) / folder / entry.name
                if target.exists() and target.read_bytes() != entry.read_bytes(): raise RuntimeError('Histórico de fila divergente; migração interrompida.')
                files.write(state + '/' + folder + '/' + entry.name, entry.read_bytes(), 0o660)
        files.write(state + '/migration-complete.json', '{"migrated":true}\n', 0o660)
    for name in ['lib.php', 'gateway.php', 'proxy.py', 'ui.js', 'nginx-route.conf']:
        text = (source / name).read_text()
        text = text.replace('/home/xui/tmp/xui-mass-queue', state)
        if name == 'lib.php': text = text.replace('chmod($temporary, 0600)', 'chmod($temporary, 0660)')
        if name == 'proxy.py':
            text = text.replace('os.umask(0o077)', 'os.umask(0o007)')
            text = text.replace('os.replace(name, path)', 'os.chmod(name, 0o660)\n        os.replace(name, path)')
            text = text.replace("if __name__ == '__main__':\n", "if __name__ == '__main__':\n    for subdir in ['', 'jobs', 'owners', 'dispatch']:\n        (STATE / subdir).mkdir(parents=True, exist_ok=True)\n    lock = STATE / 'queue.lock'\n    if not lock.exists():\n        lock.touch(mode=0o660)\n")
        files.write('/home/xui/custom-mass-queue/' + name, text, 0o640)
    pools = sorted(files.path('/home/xui/bin/php/etc').glob('[0-9]*.conf'))
    listen = re.search(r'(?m)^listen\s*=\s*(/[^\s]+)', pools[0].read_text())
    if not listen: raise RuntimeError('Socket PHP não encontrado.')
    config = dict(enabled=True, php_socket=listen.group(1), version=2, probe_origin='http://127.0.0.1:' + str(profile['http_port']))
    files.write('/home/xui/custom-mass-queue/config.json', json.dumps(config, sort_keys=True) + '\n', 0o640)
    files.write('/home/xui/admin/xui_mass_queue.php', "<?php require '/home/xui/custom-mass-queue/gateway.php';\n", 0o640)
    script = files.path('/home/xui/admin/assets/js/listings.js').read_text()
    old = (source / 'ui.js').read_text()
    new = '// BEGIN XUI_MASS_QUEUE_MANAGED_V2\n' + old + '\n// END XUI_MASS_QUEUE_MANAGED_V2\n'
    pattern = r'(?s)// BEGIN XUI_MASS_QUEUE_MANAGED_V2\n.*?// END XUI_MASS_QUEUE_MANAGED_V2\n?'
    if re.search(pattern, script): script = re.sub(pattern, lambda match: new, script)
    elif old in script: script = script.replace(old, new, 1)
    elif '/* XUI mass queue integration v1.' in script: raise RuntimeError('Outra versão da fila está no JavaScript; revisar antes de substituir.')
    else: script = script.rstrip() + '\n;\n' + new
    files.write('/home/xui/admin/assets/js/listings.js', script)
    unit = (source / 'xui-mass-queue.service').read_text().replace('After=network.target xuione.service', 'After=network.target\nBefore=xuione.service')
    unit = unit.replace('User=xui\n', 'User=xui-queue\n').replace('UMask=0077', 'UMask=0007').replace('Restart=on-failure', 'Restart=always')
    unit = re.sub(r'(?m)^MemoryMax=.*$', 'MemoryMax={}M'.format(profile['proxy_mb']), unit)
    unit = unit.replace('ReadWritePaths=/home/xui/tmp/xui-mass-queue', 'StateDirectory=xui-mass-queue\nStateDirectoryMode=0770\nReadWritePaths=' + state)
    files.write('/etc/systemd/system/xui-mass-queue.service', unit)
    for name in ['', 'jobs', 'owners', 'dispatch']:
        directory = files.path(state) / name
        directory.mkdir(parents=True, exist_ok=True); directory.chmod(0o2770)
    lock = files.path(state + '/queue.lock')
    if not lock.exists(): files.write(state + '/queue.lock', '', 0o660)


def routes(files):
    source = BASE / 'mass_queue/configure_routes.py'
    spec = importlib.util.spec_from_file_location('queue_routes', source)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    module.CONF = files.path('/home/xui/bin/nginx/conf')
    for path, text in module.changes().items(): files.write('/' + str(path.relative_to(files.root)), text)
    # Template covers future access-code generation. Reseller/API controllers keep
    # their own native filename; only #TYPE#=admin is sent through this queue.
    template = files.path('/home/xui/bin/nginx/conf/codes/template')
    if template.exists() and '# XUI mass queue template v2' not in template.read_text():
        text = template.read_text()
        match = re.search(r'(?m)^\s*location\s+~\s+\\\.php\$\s*\{', text)
        if not match: raise RuntimeError('Template de códigos não reconhecido.')
        location = module.exact_location('/#CODE#').replace('# XUI mass queue v1', '# XUI mass queue template v2')
        location = location.replace('if ($arg_action = stream_mass)', 'set $xui_mass_kind "#TYPE#:$arg_action";\n        if ($xui_mass_kind = "admin:stream_mass")')
        location = location.replace('/home/xui/admin/post.php', '/home/xui/#TYPE#/post.php')
        files.write('/home/xui/bin/nginx/conf/codes/template', text[:match.start()] + location + text[match.start():])


def prepare(files, profile, fresh=False):
    configure_mysql(files, profile, fresh)
    etc = files.path('/home/xui/bin/php/etc')
    for path in list(etc.glob('[0-9]*.conf')) + [etc / 'template']:
        if path.exists(): files.write('/' + str(path.relative_to(files.root)), set_option(path.read_text(), 'pm.max_children', profile['php_children']))
    redis = files.path('/home/xui/bin/redis/redis.conf')
    if not redis.exists(): raise RuntimeError('Configuração autenticada do Redis ainda não foi criada.')
    text = re.sub(r'(?m)^\s*maxmemory\s+.*\n?', '', redis.read_text()).rstrip() + '\nmaxmemory ' + str(profile['redis_mb']) + 'mb\n'
    files.write('/home/xui/bin/redis/redis.conf', text, 0o600)
    for name in ['source_guard_http.conf', 'source_guard_rules.conf', 'source_guard_server.conf']:
        text = (BASE / 'reference/home/xui/bin/nginx/conf' / name).read_text()
        if name == 'source_guard_http.conf': text = text.replace('rate=200r/s', 'rate=' + str(profile['open_rate']) + 'r/s')
        if name == 'source_guard_rules.conf':
            text = text.replace('opens 24;', 'opens ' + str(profile['channel_pending']) + ';').replace('opens 160;', 'opens ' + str(profile['open_pending']) + ';')
            text = text.replace('burst=400', 'burst=' + str(profile['open_rate'] * 2))
        files.write('/home/xui/bin/nginx/conf/' + name, text)
    nginx = files.path('/home/xui/bin/nginx/conf/nginx.conf')
    text = nginx.read_text()
    if 'include source_guard_http.conf;' not in text: text = re.sub(r'\bhttp\s*\{', 'http {\n    include source_guard_http.conf;', text, count=1)
    text = re.sub(r'worker_processes\s+(?:auto|\d+)\s*;', 'worker_processes ' + str(profile['nginx_workers']) + ';', text)
    text = re.sub(r'(thread_pool\s+pool_xui\s+threads=)\d+', lambda match: match.group(1) + str(profile['aio_threads']), text)
    files.write('/home/xui/bin/nginx/conf/nginx.conf', text)
    custom = files.path('/home/xui/bin/nginx/conf/custom.conf')
    text = custom.read_text() if custom.exists() else ''
    if 'include source_guard_server.conf;' not in text: files.write('/home/xui/bin/nginx/conf/custom.conf', text.rstrip() + '\ninclude source_guard_server.conf;\n')
    for unit, memory in [('xuione', profile['xui_mb']), ('mariadb', profile['mysql_mb'])]:
        text = (BASE / ('reference/etc/systemd/system/' + unit + '.service.d/90-xui-stability.conf')).read_text()
        text += '\nMemoryMax=' + str(memory) + 'M\n'
        if unit == 'xuione':
            text = text.replace('Wants=mariadb.service', 'Wants=mariadb.service xui-mass-queue.service').replace('After=mariadb.service', 'After=mariadb.service xui-mass-queue.service')
            text += 'LimitNOFILE=655350\nTimeoutStartSec=120\nExecStartPre=/usr/bin/python3 /home/xui/custom-updates/apply.py --prepare\nExecStartPost=/usr/bin/python3 /home/xui/custom-updates/apply.py --finalize\n'
        files.write('/etc/systemd/system/' + unit + '.service.d/90-xui-stability.conf', text)
    for path in (BASE / 'reference/etc/systemd/system').glob('xui-*.*'):
        if path.is_file(): files.write('/etc/systemd/system/' + path.name, path.read_bytes())
    for name in ['xui-healthcheck', 'xui-mariadb-watchdog']:
        files.write('/usr/local/sbin/' + name, (BASE / name).read_bytes(), 0o750)
    files.write('/etc/logrotate.d/xui-runtime', (BASE / 'reference/etc/logrotate.d/xui-runtime').read_bytes())
    mysqlrotate = files.path('/etc/logrotate.d/mysql-server')
    if mysqlrotate.exists():
        text = mysqlrotate.read_text()
        text = re.sub(r'(?m)^[ \t]*maxsize[ \t]+\S+[ \t]*\r?\n', '', text)
        if 'mariadb-slow.log' not in text: text = text.replace('{', '/var/log/mysql/mariadb-slow.log {', 1)
        text = text.replace('{', '{\n\tmaxsize 64M', 1)
        files.write('/etc/logrotate.d/mysql-server', text)
    else: files.write('/etc/logrotate.d/mysql-server', (BASE / 'reference/etc/logrotate.d/mysql-server').read_bytes())
    files.write('/etc/sysctl.d/99-xui-stability.conf', 'vm.overcommit_memory = 1\n')
    files.write('/etc/xui-stability/profile.json', json.dumps(profile, indent=2, sort_keys=True) + '\n', 0o600)
    queue_assets(files, profile); routes(files)


def ownership(files):
    xui = pwd.getpwnam('xui'); queue = pwd.getpwnam('xui-queue'); group = grp.getgrnam('xui')
    for path in files.path('/home/xui/custom-mass-queue').rglob('*'):
        os.chown(path, 0, group.gr_gid)
    os.chown(files.path('/home/xui/custom-mass-queue'), 0, group.gr_gid)
    files.path('/home/xui/custom-mass-queue').chmod(0o750)
    os.chown(files.path('/home/xui/admin/xui_mass_queue.php'), 0, group.gr_gid)
    for path in [files.path('/var/lib/xui-mass-queue')] + list(files.path('/var/lib/xui-mass-queue').rglob('*')):
        os.chown(path, queue.pw_uid if path.is_dir() else xui.pw_uid, group.gr_gid)
        path.chmod(0o2770 if path.is_dir() else 0o660)
    for name in ['redis/redis.conf', 'nginx/conf/source_guard_http.conf', 'nginx/conf/source_guard_rules.conf', 'nginx/conf/source_guard_server.conf']:
        os.chown(files.path('/home/xui/bin/' + name), xui.pw_uid, group.gr_gid)
    if Path('/home/xui/config/ubuntu-compat.json').exists():
        for name in ['/var/lib/nginx','/var/log/nginx']:
            directory=Path(name);directory.mkdir(parents=True,exist_ok=True)
            for path in [directory]+list(directory.rglob('*')):os.chown(path,xui.pw_uid,group.gr_gid)
    Path('/var/log/mysql').mkdir(exist_ok=True)
    mysql = pwd.getpwnam('mysql'); adm = grp.getgrnam('adm')
    slow = Path('/var/log/mysql/mariadb-slow.log'); slow.touch(exist_ok=True); os.chown(slow, mysql.pw_uid, adm.gr_gid); slow.chmod(0o640)


def validate(files):
    # Native launch runs nginx as xui; allow only its two binaries to bind low ports.
    for binary in ['/home/xui/bin/nginx/sbin/nginx', '/home/xui/bin/nginx_rtmp/sbin/nginx_rtmp']:
        target='/usr/sbin/nginx' if Path('/home/xui/config/ubuntu-compat.json').exists() else binary
        run(['setcap','cap_net_bind_service=+ep',target])
    run(['runuser','-u','xui','--','/home/xui/bin/nginx/sbin/nginx','-t'])
    for path in files.path('/home/xui/bin/php/etc').glob('[0-9]*.conf'):
        run(['runuser','-u','xui','--','/home/xui/bin/php/sbin/php-fpm','-t','-y',path])
    run(['mysqld','--verbose','--help'])
    run(['logrotate','-d','/etc/logrotate.d/mysql-server','/etc/logrotate.d/xui-runtime'])
    run(['/usr/local/sbin/xui-mariadb-watchdog','--self-test'])
    for unit in ['xuione','mariadb']:
        budget = json.loads(files.path('/etc/xui-stability/profile.json').read_text())[unit.replace('xuione','xui').replace('mariadb','mysql') + '_mb'] * 1048576
        result = run(['systemctl','show',unit+'.service','--property=MemoryCurrent','--value']).stdout.strip()
        if result.isdigit() and int(result) > budget * .9: raise RuntimeError('Memória atual de '+unit+' está próxima/acima do novo teto. Aplique em manutenção.')


def finalize(profile, changed):
    # No SQL writes to application tables: only connection/temporary/diagnostic settings.
    sql = 'SET GLOBAL max_connections={}; SET GLOBAL tmp_table_size=67108864; SET GLOBAL max_heap_table_size=67108864; SET GLOBAL long_query_time=2; SET GLOBAL slow_query_log_file="/var/log/mysql/mariadb-slow.log"; SET GLOBAL slow_query_log=1;'.format(profile['mysql_connections'])
    run(['mysql','--protocol=socket','-uroot'], input=sql)
    settings = {}
    for line in Path('/home/xui/bin/redis/redis.conf').read_text().splitlines():
        values = shlex.split(line, comments=True)
        if len(values) > 1: settings[values[0]] = values[1:]
    with socket.create_connection(('127.0.0.1', int(settings.get('port',['6379'])[0])), timeout=3) as connection:
        reader = connection.makefile('rb')
        def redis_command(*values):
            values = [str(value).encode() for value in values]
            connection.sendall(b'*'+str(len(values)).encode()+b'\r\n'+b''.join(b'$'+str(len(value)).encode()+b'\r\n'+value+b'\r\n' for value in values))
            if not reader.readline().startswith(b'+OK'): raise RuntimeError('Não foi possível aplicar o teto do Redis.')
        if settings.get('requirepass'): redis_command('AUTH', settings['requirepass'][0])
        redis_command('CONFIG', 'SET', 'maxmemory', profile['redis_mb'] * 1048576)
    run(['systemctl','daemon-reload'])
    for unit, budget in [('xuione',profile['xui_mb']),('mariadb',profile['mysql_mb'])]:
        run(['systemctl','set-property','--runtime',unit+'.service','MemoryMax='+str(budget)+'M'])
    run(['systemctl','enable','--now','xui-healthcheck.timer','xui-logrotate.timer','xui-mariadb-watchdog.timer'])
    run(['sysctl','-p','/etc/sysctl.d/99-xui-stability.conf'])
    for attempt in range(80):
        if Path('/home/xui/bin/nginx/logs/nginx.pid').exists() and Path('/home/xui/bin/php/sockets/1.sock').exists(): break
        time.sleep(.5)
    else: raise RuntimeError('Nginx/PHP não ficaram disponíveis.')
    if any('/nginx/conf/' in path for path in changed):
        run(['runuser','-u','xui','--','/home/xui/bin/nginx/sbin/nginx','-s','reload'])
    # Reload only verified PHP masters, not all processes owned by xui.
    for path in (Path('/home/xui/bin/php/sockets').glob('*.pid') if any('/php/etc/' in path for path in changed) else []):
        pid = int(path.read_text()); cmd = Path('/proc/{}/cmdline'.format(pid)).read_bytes()
        if b'php-fpm: master process' in cmd: os.kill(pid, signal.SIGUSR2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/'))
    parser.add_argument('--stage', action='store_true', help='Generate files only in an isolated root')
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--finalize', action='store_true')
    parser.add_argument('--fresh', action='store_true')
    parser.add_argument('--memory-mb', type=int)
    parser.add_argument('--cpus', type=int)
    args = parser.parse_args(); root = args.root.resolve()
    if root != Path('/') and not args.stage: raise RuntimeError('An alternate root requires --stage')
    if root == Path('/') and args.stage: raise RuntimeError('Staging cannot target the real system')
    if not args.stage and os.geteuid() != 0: raise RuntimeError('Execute como root')
    files = Files(root)
    pools = len(list(files.path('/home/xui/bin/php/etc').glob('[0-9]*.conf')))
    saved = files.path('/etc/xui-stability/profile.json')
    memory = args.memory_mb or effective_memory()
    # ExecStartPre/ExecStartPost must not repeatedly derive smaller budgets from
    # the XUI service's own newly imposed memory limit.
    if saved.exists() and (args.prepare or args.finalize) and not args.fresh and not args.memory_mb:
        physical = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemTotal:'))) // 1024
        memory = min(json.loads(saved.read_text())['memory_mb'], physical)
    profile = budgets(memory, args.cpus or effective_cpus(), pools)
    ports = files.path('/home/xui/bin/nginx/conf/ports')
    for path in ports.glob('*.conf'):
        match = re.search(r'listen\s+(?:[0-9.]+:)?(\d+)\s*;', path.read_text())
        if match: profile['http_port'] = int(match.group(1)); break
    try:
        prepare(files, profile, args.fresh)
        if not args.stage: ownership(files); validate(files)
        files.save()
        if args.finalize: finalize(profile, files.changed)
        if files.changed or args.stage: print(json.dumps(dict(profile=profile, changed=len(files.changed), backup=str(files.backup) if files.records else None)))
    except Exception:
        files.save(); files.rollback()
        raise


if __name__ == '__main__':
    try: main()
    except Exception as error: raise SystemExit('ERRO: ' + str(error))
