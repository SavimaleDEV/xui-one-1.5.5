#!/usr/bin/env python3
"""Restore one update file backup during scheduled maintenance."""
import argparse,hashlib,json,os,shutil,subprocess
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('backup',type=Path);p.add_argument('--check',action='store_true');a=p.parse_args()
    if os.geteuid()!=0:raise SystemExit('Execute como root')
    records=json.loads((a.backup/'manifest.json').read_text())
    for name,record in records.items():
        path=Path(name)
        if path.is_absolute() or '..' in path.parts:raise SystemExit('Manifesto inválido')
        target=Path('/')/path
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest()!=record.get('installed_sha256'):raise SystemExit('Arquivo alterado depois do backup: '+name)
    if a.check:print('Hashes conferidos; nenhuma alteração executada.');return
    if subprocess.run(['systemctl','is-active','--quiet','xuione.service']).returncode==0:raise SystemExit('Pare o XUI em manutenção antes de restaurar os arquivos.')
    if Path('/var/lib/xui-mass-queue/lease.json').exists():raise SystemExit('Há lote pendente na fila; conferir antes da reversão.')
    for name,record in reversed(list(records.items())):
        target=Path('/')/name
        if record['existed']:
            shutil.copy2(a.backup/'files'/name,target);os.chown(target,record['uid'],record['gid']);target.chmod(record['mode'])
        else:target.unlink()
    subprocess.run(['systemctl','daemon-reload'],check=True)
    print('Arquivos restaurados. Confira serviços e valores dinâmicos antes de iniciar o XUI.')

if __name__=='__main__':main()
