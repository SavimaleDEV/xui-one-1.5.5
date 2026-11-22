<?php
// Shared, private queue state. The original encoded XUI PHP files stay in place.
const XMQ_ROOT = '/home/xui/custom-mass-queue';
const XMQ_STATE = '/home/xui/tmp/xui-mass-queue';

function xmq_reply($value, $status = 200) {
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    echo json_encode($value, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}
function xmq_fail($message, $status = 400) { xmq_reply(['ok'=>false,'message'=>$message], $status); }
function xmq_read($path) {
    if (!is_file($path)) return null;
    $data = json_decode(file_get_contents($path), true);
    if (!is_array($data)) throw new RuntimeException('Estado da fila inválido.');
    return $data;
}
function xmq_write($path, $data) {
    $temporary = $path . '.' . bin2hex(random_bytes(6)) . '.tmp';
    $encoded = json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
    if (file_put_contents($temporary, $encoded, LOCK_EX) === false) throw new RuntimeException('Não foi possível salvar a fila.');
    chmod($temporary, 0600);
    if (!rename($temporary, $path)) throw new RuntimeException('Não foi possível confirmar a fila.');
}
function xmq_job_path($id) {
    if (!is_string($id) || !preg_match('/^[a-f0-9]{32}$/D', $id)) xmq_fail('Fila inválida.');
    return XMQ_STATE . '/jobs/' . $id . '.json';
}
function xmq_lock() {
    $handle = fopen(XMQ_STATE . '/queue.lock', 'c');
    if (!$handle || !flock($handle, LOCK_EX)) throw new RuntimeException('Fila indisponível.');
    return $handle;
}
function xmq_rows($query, ...$args) {
    if (!XUI::$db->query($query, ...$args)) throw new RuntimeException('Não foi possível consultar os canais.');
    return XUI::$db->get_rows() ?: [];
}
function xmq_auth() {
    $id = $_SESSION['hash'] ?? '';
    if (!isset($_COOKIE[session_name()]) || !is_scalar($id) || !ctype_digit((string)$id) || intval($id) < 1 ||
        !preg_match('/^[a-fA-F0-9]{32}$/D', (string)($_SESSION['verify'] ?? ''))) xmq_fail('Entre no painel para continuar.', 401);
    $users = xmq_rows('SELECT u.id,u.status,g.is_admin,g.allowed_pages FROM users u JOIN users_groups g ON g.group_id=u.member_group_id WHERE u.id=? LIMIT 1', intval($id));
    $user = $users[0] ?? null;
    if (!$user || intval($user['status']) !== 1 || intval($user['is_admin']) !== 1) xmq_fail('Acesso não permitido.', 403);
    $allowed = json_decode($user['allowed_pages'] ?: '[]', true);
    if (!is_array($allowed) || ($allowed && !in_array('stream_mass', $allowed, true))) xmq_fail('Sem permissão para editar streams em massa.', 403);
    if (!empty(XUI::$rSettings['ip_logout']) && ($_SESSION['ip'] ?? '') !== ($_SERVER['REMOTE_ADDR'] ?? '')) xmq_fail('A sessão mudou de endereço. Entre novamente.', 401);
    if (empty($_SESSION['xui_mass_queue_csrf'])) $_SESSION['xui_mass_queue_csrf'] = bin2hex(random_bytes(32));
    $identity = ['id'=>intval($user['id']), 'csrf'=>$_SESSION['xui_mass_queue_csrf'], 'session'=>hash('sha256', session_id())];
    $_SESSION['last_activity'] = (string)time();
    session_write_close();
    return $identity;
}
function xmq_payload($entries) {
    if (!is_array($entries) || count($entries) > 256) xmq_fail('Formulário inválido.');
    $parts = [];
    foreach ($entries as $entry) {
        if (!is_array($entry) || count($entry) !== 2 || !is_string($entry[0]) || !is_string($entry[1]) ||
            !preg_match('/^[A-Za-z0-9_-]+(?:\[\])?$/D', $entry[0]) || strlen($entry[1]) > 131072) xmq_fail('Campo inválido no formulário.');
        $parts[] = rawurlencode($entry[0]) . '=' . rawurlencode($entry[1]);
    }
    parse_str(implode('&', $parts), $post);
    if (empty($post['c_server_tree']) || ($post['server_type'] ?? '') !== 'SET') xmq_fail('Use SET SERVERS para a troca controlada.');
    foreach ($post as $name=>$value) if (strpos($name, 'c_') === 0 && $name !== 'c_server_tree' && !empty($value)) xmq_fail('Nesta troca, marque somente a opção Servidores. Faça as outras edições separadamente.');
    $tree = json_decode($post['server_tree_data'] ?? '', true);
    if (!is_array($tree)) xmq_fail('Selecione o servidor de destino.');
    $targets = array_values(array_filter($tree, function($node) { return is_array($node) && ($node['parent'] ?? null) !== '#'; }));
    if (count($targets) !== 1 || ($targets[0]['parent'] ?? '') !== 'source' || !preg_match('/^[1-9][0-9]*$/D', (string)($targets[0]['id'] ?? ''))) xmq_fail('Selecione um único servidor de destino.');
    foreach (($post['on_demand'] ?? []) as $value) if ((string)$value !== '0' && (string)$value !== '') xmq_fail('A troca controlada de canais sob demanda ainda não está disponível.');
    $ids = json_decode($post['streams'] ?? '', true);
    if (!is_array($ids) || !$ids || count($ids) > 2000) xmq_fail('Selecione entre 1 e 2.000 canais.');
    $seen = [];
    foreach ($ids as &$id) {
        if ((!is_string($id) && !is_int($id)) || !preg_match('/^[1-9][0-9]*$/D', (string)$id) || intval($id) > 2147483647 || isset($seen[(string)$id])) xmq_fail('A seleção contém IDs inválidos ou repetidos.');
        $id = (string)$id; $seen[$id] = true;
    }
    unset($id);
    $target = intval($targets[0]['id']);
    $servers = xmq_rows('SELECT id,enabled,status FROM servers WHERE id=? AND server_type=0 LIMIT 1', $target);
    if (!$servers || intval($servers[0]['enabled']) !== 1 || intval($servers[0]['status']) !== 1) xmq_fail('O servidor de destino não está disponível.');
    $existing = xmq_rows('SELECT id FROM streams WHERE id IN (' . implode(',', array_map('intval', $ids)) . ')');
    if (count($existing) !== count($ids)) xmq_fail('Algum canal selecionado não existe mais. Atualize a lista.');
    $post['streams'] = json_encode($ids);
    return [$post, $ids, $target, !empty($post['restart_on_edit'])];
}
function xmq_public($job) {
    return ['id'=>$job['id'], 'mode'=>$job['mode'], 'phase'=>$job['phase'], 'total'=>count($job['ids']),
        'done'=>$job['cursor'], 'submitted'=>$job['cursor'] + (isset($job['batch']) && in_array($job['phase'], ['dispatched','waiting','blocked','uncertain'], true) ? count($job['batch']['ids']) : 0),
        'current'=>isset($job['batch']) ? $job['batch']['ids'] : [], 'pending'=>array_slice($job['ids'], $job['cursor'] + (isset($job['batch']) ? count($job['batch']['ids']) : 0)),
        'target'=>$job['target'], 'stopRequested'=>!empty($job['stop']), 'message'=>$job['message'] ?? '', 'created'=>$job['created']];
}
function xmq_release(&$job, $finished) {
    if (isset($job['batch']['token_hash'])) @unlink(XMQ_STATE . '/dispatch/' . $job['batch']['token_hash'] . '.json');
    if ($finished && isset($job['batch'])) $job['cursor'] += count($job['batch']['ids']);
    unset($job['batch']);
    $job['phase'] = !empty($job['stop']) ? 'stopped' : ($job['cursor'] === count($job['ids']) ? 'complete' : 'queued');
    $lease = xmq_read(XMQ_STATE . '/lease.json');
    if ($lease && $lease['job'] === $job['id']) @unlink(XMQ_STATE . '/lease.json');
    xmq_write(xmq_job_path($job['id']), $job);
}
function xmq_refresh(&$job) {
    if (!isset($job['batch'])) return;
    $now = time();
    if ($job['phase'] === 'reserved') {
        if ($now - $job['batch']['created'] > 180 || !empty($job['stop'])) {
            $job['stop'] = true; $job['message'] = 'O envio não foi iniciado. Nenhum lote será reenviado automaticamente.';
            xmq_release($job, false);
        }
        return;
    }
    if ($job['phase'] === 'dispatched' && $now - ($job['batch']['dispatched'] ?? $now) > 125) {
        $job['phase'] = 'uncertain'; $job['message'] = 'O envio perdeu a confirmação. Conferindo o lote sem reenviar.';
    }
    if (!in_array($job['phase'], ['waiting','blocked','uncertain'], true)) return;
    $rows = xmq_rows('SELECT stream_id,server_id,pid,monitor_pid,stream_status,bitrate,on_demand FROM streams_servers WHERE stream_id IN (' . implode(',', array_map('intval', $job['batch']['ids'])) . ')');
    $byId = []; foreach ($rows as $row) $byId[(string)$row['stream_id']][] = $row;
    $all = true; $allFailed = true;
    foreach ($job['batch']['ids'] as $id) {
        $assigned = $byId[$id] ?? [];
        $row = count($assigned) === 1 && intval($assigned[0]['server_id']) === $job['target'] ? $assigned[0] : null;
        if ($job['mode'] === 'metadata') {
            if (!$row) $all = false;
            continue;
        }
        $pid = $row ? intval($row['pid']) : 0;
        $was = intval($job['batch']['baseline'][$id] ?? 0);
        $candidate = $row && $pid > 0 && intval($row['monitor_pid']) > 0 && intval($row['stream_status']) === 0 && floatval($row['bitrate']) > 0 && ($was <= 0 || $pid !== $was);
        if ($candidate) {
            $seen = $job['batch']['observed'][$id] ?? null;
            if (!$seen || $seen['pid'] !== $pid) $job['batch']['observed'][$id] = ['pid'=>$pid,'since'=>$now];
            $settle = max(2, min(60, intval(XUI::$rSettings['seg_time'] ?? 10) + 1));
            if ($now - $job['batch']['observed'][$id]['since'] < $settle) $all = false;
        } else { unset($job['batch']['observed'][$id]); $all = false; }
        if (!$row || intval($row['stream_status']) !== 1 || $pid > 0) $allFailed = false;
    }
    if ($all) { $job['message'] = ''; xmq_release($job, true); return; }
    if (!empty($job['stop']) && $allFailed) { xmq_release($job, false); return; }
    if ($now - ($job['batch']['dispatched'] ?? $job['batch']['created']) > 180) {
        $job['phase'] = 'blocked';
        $job['message'] = 'Este lote ainda não confirmou atividade no destino. A fila está pausada; confira os canais indicados.';
    }
    xmq_write(xmq_job_path($job['id']), $job);
}
function xmq_active() {
    $lease = xmq_read(XMQ_STATE . '/lease.json');
    if (!$lease) return null;
    $job = xmq_read(xmq_job_path($lease['job']));
    if (!$job) throw new RuntimeException('Reserva da fila sem estado.');
    xmq_refresh($job);
    return xmq_read(XMQ_STATE . '/lease.json');
}
function xmq_guard_health() {
    $config = xmq_read(XMQ_ROOT . '/config.json');
    $prefix = rtrim(dirname($_SERVER['SCRIPT_NAME']), '/');
    $url = ($config['probe_origin'] ?? 'http://127.0.0.1:80') . $prefix . '/post.php?action=stream_mass';
    $context = stream_context_create(['http'=>['method'=>'POST','header'=>"Content-Type: application/x-www-form-urlencoded\r\nConnection: close\r\n",'content'=>'c_server_tree=on&server_type=SET&streams=%5B%5D','timeout'=>3,'ignore_errors'=>true]]);
    @file_get_contents($url, false, $context);
    $headers = $http_response_header ?? [];
    $status = $headers[0] ?? '';
    $marker = false;
    foreach ($headers as $header) if (strtolower(trim($header)) === 'x-xui-mass-guard: 1') $marker = true;
    if (strpos($status, ' 403 ') === false || !$marker) xmq_fail('A proteção da fila não está respondendo. O próximo lote foi bloqueado; recarregue o painel ou confira o serviço.', 503);
}
