"""Prepare an explicitly requested clean installation on a dedicated XUI host."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

DATABASES = ('xui', 'xui_migrate')
TMPFS = {'/home/xui/tmp', '/home/xui/content/streams'}
HOME = Path('/home/xui')
CREDENTIALS = Path('/root/xui-install-credentials.txt')
ARCHIVE = (HOME, Path('/var/lib/xui-mass-queue'), CREDENTIALS,
           Path('/var/backups/xui-initial-content-streams'), Path('/var/backups/xui-initial-tmp'))


def scoped(path):
    return any(path == str(root) or path.startswith(str(root) + '/') for root in ARCHIVE)


def runtime_mounts(mountinfo, fstab):
    active = []
    for line in mountinfo.splitlines():
        before, after = line.split(' - ', 1)
        path = before.split()[4]
        if scoped(path):
            if path not in TMPFS or after.split()[0] != 'tmpfs' or path in active:
                raise RuntimeError('Montagem externa ou sobreposta no XUI: ' + path)
            active.append(path)
    for line in fstab.splitlines():
        cols = line.partition('#')[0].split()
        if len(cols) >= 3 and scoped(cols[1]) and (cols[1] not in TMPFS or cols[2] != 'tmpfs'):
            raise RuntimeError('Montagem externa no fstab: ' + cols[1])
    return active


def reset(command, sql):
    names = set(sql("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME NOT IN ('mysql','information_schema','performance_schema','sys');").splitlines())
    if names.difference(DATABASES):
        raise RuntimeError('Há bancos de outras aplicações neste servidor.')
    existing = [name for name in DATABASES if name in names]
    dump = shutil.which('mariadb-dump') or shutil.which('mysqldump')
    if existing and not dump:
        raise RuntimeError('Ferramenta de backup do MariaDB ausente.')
    mounts = runtime_mounts(Path('/proc/self/mountinfo').read_text(), Path('/etc/fstab').read_text())
    backup = Path(tempfile.mkdtemp(prefix='xui-reset-', dir='/var/backups'))
    print('Backup da reinstalação:', backup, flush=True)
    units = ['xuione.service', 'xui-mass-queue.service']
    for name in ['xui-mariadb-watchdog', 'xui-healthcheck', 'xui-logrotate']:
        units.extend([name + '.timer', name + '.service'])
    rows = command(['systemctl','list-units','--all','--no-legend','--no-pager','--plain'] + units).stdout
    loaded = [row.split()[0] for row in rows.splitlines() if row.split() and row.split()[0] in units]
    if loaded:
        command(['systemctl','stop'] + loaded)
    if existing:
        with (backup/'bancos.sql').open('xb') as out:
            subprocess.run([dump,'--protocol=socket','-uroot','--single-transaction','--routines',
                            '--events','--triggers','--databases'] + existing, stdout=out, check=True)
            out.flush()
            os.fsync(out.fileno())
    for path in mounts:
        command(['umount',path])
    old_user = None
    if CREDENTIALS.is_file() and not CREDENTIALS.is_symlink() and CREDENTIALS.stat().st_uid == 0:
        match = re.search(r'^MySQL username: (xui_[0-9a-f]{20})$', CREDENTIALS.read_text(), re.M)
        if match:
            old_user = match.group(1)
    # Keep each move on its own filesystem, including large media directories.
    with (backup/'arquivos.txt').open('x') as index:
        for path in ARCHIVE:
            if os.path.lexists(path):
                folder = Path(tempfile.mkdtemp(prefix='.xui-anterior-', dir=str(path.parent)))
                target = folder/path.name
                index.write(str(target) + '\n')
                index.flush()
                path.rename(target)
    if old_user:
        sql("DROP USER IF EXISTS '{0}'@'localhost', '{0}'@'127.0.0.1'".format(old_user))
    for name in existing:
        sql('DROP DATABASE `' + name + '`')
