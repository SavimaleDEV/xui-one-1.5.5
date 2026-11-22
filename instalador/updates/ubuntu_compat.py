"""Adapt the supplied PHP 7.4 runtime to Ubuntu 22.04/24.04."""
from pathlib import Path
import json
import os
import shutil
import subprocess

SUPPORTED=('20.04','22.04','24.04')

def release(path=Path('/etc/os-release')):
    values=dict(line.split('=',1) for line in path.read_text().splitlines() if '=' in line)
    values={k:v.strip('"') for k,v in values.items()}
    if values.get('ID')!='ubuntu' or values.get('VERSION_ID') not in SUPPORTED:
        raise RuntimeError('Use Ubuntu 20.04, 22.04 ou 24.04 x86_64.')
    return values['VERSION_ID']

def packages(common,version):
    result=list(common)
    if version!='20.04':
        result.remove('libssl1.1')
        result+=['nginx-full','libnginx-mod-rtmp']
    if version=='24.04':
        mapping={'libcurl4':'libcurl4t64','libpng16-16':'libpng16-16t64','libssh2-1':'libssh2-1t64'}
        result=[mapping.get(p,p) for p in result]
    return result

def prepare(version,components,root=Path('/'),execute=True):
    if version=='20.04':return
    root=Path(root);base=root/'home/xui';lib=root/'usr/local/lib/xui-compat'
    lib.mkdir(parents=True,exist_ok=True)
    for name in ['libssl.so.1.1','libcrypto.so.1.1']:
        shutil.copyfile(Path(components)/name,lib/name);(lib/name).chmod(0o644)
    conf=root/'etc/ld.so.conf.d/xui-compat.conf';conf.parent.mkdir(parents=True,exist_ok=True)
    conf.write_text('/usr/local/lib/xui-compat\n')
    for directory,binary in [('nginx','nginx'),('nginx_rtmp','nginx_rtmp')]:
        target=base/'bin'/directory/'sbin'/binary
        saved=target.with_name(target.name+'.vendor-original')
        if not saved.exists():shutil.copy2(target,saved)
        target.write_text('#!/bin/sh\nexec /usr/sbin/nginx -p /home/xui/bin/{d}/ -c conf/nginx.conf -g "pid /home/xui/bin/{d}/logs/nginx.pid;" "$@"\n'.format(d=directory));target.chmod(0o755)
        config=base/'bin'/directory/'conf/nginx.conf'
        text=config.read_text()
        if not text.startswith('# XUI Ubuntu compatibility'):
            if directory=='nginx_rtmp':text='load_module /usr/lib/nginx/modules/ngx_rtmp_module.so;\n'+text
            text='# XUI Ubuntu compatibility\nerror_log /home/xui/logs/'+directory+'-compat-error.log warn;\n'+text
            if directory=='nginx':
                text=text.replace('http {','http {\n    client_body_temp_path /home/xui/tmp/nginx-client-body;\n    proxy_temp_path /home/xui/tmp/nginx-proxy;\n    fastcgi_temp_path /home/xui/tmp/nginx-fastcgi;\n    uwsgi_temp_path /home/xui/tmp/nginx-uwsgi;\n    scgi_temp_path /home/xui/tmp/nginx-scgi;',1)
            config.write_text(text)
    (base/'config/ubuntu-compat.json').write_text(json.dumps({'ubuntu':version,'nginx':'distribution','openssl_compat':'1.1.1f-1ubuntu2.24'})+'\n')
    if execute:
        info=base.stat()
        for name in ['/var/lib/nginx','/var/log/nginx']:
            folder=root/name.lstrip('/');folder.mkdir(parents=True,exist_ok=True)
            for path in [folder]+list(folder.rglob('*')):os.chown(path,info.st_uid,info.st_gid)
        subprocess.run(['ldconfig'],check=True)
