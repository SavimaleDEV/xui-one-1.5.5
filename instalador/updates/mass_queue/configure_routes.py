"""Prepare Nginx routes without changing the protected native PHP controller."""
from pathlib import Path
import re

CONF = Path('/home/xui/bin/nginx/conf')
MARKER = '# XUI mass queue v1'


def exact_location(prefix):
    return '''
    # XUI mass queue v1
    location = PREFIX/post.php {
        error_page 418 = @xui_mass_queue_post;
        if ($arg_action = stream_mass) { return 418; }
        limit_req zone=one burst=500 nodelay;
        include fastcgi_params;
        fastcgi_pass php;
        fastcgi_buffering on;
        fastcgi_buffers 96 32k;
        fastcgi_buffer_size 32k;
        fastcgi_max_temp_file_size 0;
        fastcgi_keep_conn on;
        fastcgi_param SCRIPT_FILENAME /home/xui/admin/post.php;
        fastcgi_param SCRIPT_NAME $fastcgi_script_name;
    }
'''.replace('PREFIX', prefix)


def changes():
    edits = {}
    found = 0
    for path in (CONF / 'codes').glob('*.conf'):
        text = path.read_text()
        if not re.search(r'alias\s+/home/xui/admin\s*;', text):
            continue
        found += 1
        if MARKER in text or '# XUI mass queue template v2' in text:
            continue
        prefix = re.search(r'location\s+\^~\s+(/[A-Za-z0-9_-]+)\s*\{', text)
        nested = re.search(r'(?m)^\s*location\s+~\s+\\\.php\$\s*\{', text)
        if not prefix or not nested:
            raise RuntimeError('Unrecognized admin location: ' + path.name)
        edits[path] = text[:nested.start()] + exact_location(prefix.group(1)) + text[nested.start():]
    if not found:
        raise RuntimeError('No admin UI aliases found')
    custom = CONF / 'custom.conf'
    text = custom.read_text() if custom.exists() else ''
    include = 'include /home/xui/custom-mass-queue/nginx-route.conf;'
    if include not in text:
        edits[custom] = text + '\n' + MARKER + '\n' + include + '\n'
    return edits


if __name__ == '__main__':
    edits = changes()
    for path, text in edits.items():
        path.write_text(text)
    print('Prepared controlled mass-edit routing:', len(edits), 'configuration files')
