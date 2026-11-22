<?php
require '/home/xui/custom-mass-queue/lib.php';
try {
    ob_start();
    require_once '/home/xui/includes/admin.php';
    ob_end_clean();
    $identity = xmq_auth();
    $config = xmq_read(XMQ_ROOT . '/config.json');
    if (!$config || empty($config['enabled'])) xmq_fail('A fila está em manutenção.', 503);
    $op = $_GET['op'] ?? 'info';
    $input = [];
    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        if (!hash_equals($identity['csrf'], (string)($_SERVER['HTTP_X_XUI_CSRF'] ?? ''))) xmq_fail('Atualize a sessão do painel.', 403);
        if (intval($_SERVER['CONTENT_LENGTH'] ?? 0) > 524288) xmq_fail('Formulário muito grande.', 413);
        $input = json_decode(file_get_contents('php://input'), true);
        if (!is_array($input)) xmq_fail('Requisição inválida.');
    } elseif (!in_array($op, ['info','status','check'], true)) xmq_fail('Método inválido.', 405);
    if ($op === 'check') { xmq_guard_health(); xmq_reply(['ok'=>true,'guard'=>true]); }
    $lock = xmq_lock();
    $lease = xmq_active();
    $ownerPath = XMQ_STATE . '/owners/' . $identity['id'] . '.json';
    $owner = xmq_read($ownerPath);
    if ($op === 'info') {
        $job = $owner ? xmq_read(xmq_job_path($owner['job'])) : null;
        if ($job) xmq_refresh($job);
        xmq_reply(['ok'=>true,'csrf'=>$identity['csrf'],'capacity'=>2,'job'=>$job ? xmq_public($job) : null]);
    }
    if ($op === 'create') {
        $existing = $owner ? xmq_read(xmq_job_path($owner['job'])) : null;
        if ($existing && !in_array($existing['phase'], ['complete','stopped'], true)) xmq_reply(['ok'=>true,'job'=>xmq_public($existing),'existing'=>true]);
        [$post,$ids,$target,$restart] = xmq_payload($input['entries'] ?? null);
        $id = bin2hex(random_bytes(16));
        $job = ['id'=>$id,'owner'=>$identity['id'],'created'=>time(),'mode'=>$restart?'transfer':'metadata',
            'phase'=>'queued','cursor'=>0,'ids'=>$ids,'target'=>$target,'post'=>$post,'stop'=>false,'message'=>''];
        xmq_write(xmq_job_path($id), $job); xmq_write($ownerPath, ['job'=>$id]);
        xmq_reply(['ok'=>true,'job'=>xmq_public($job)]);
    }
    $id = $_GET['id'] ?? ($input['id'] ?? '');
    $job = xmq_read(xmq_job_path($id));
    if (!$job || $job['owner'] !== $identity['id']) xmq_fail('Fila não encontrada.', 404);
    xmq_refresh($job);
    if ($op === 'cancel') {
        $job['stop'] = true;
        if (!isset($job['batch'])) $job['phase'] = 'stopped';
        xmq_write(xmq_job_path($id), $job); xmq_refresh($job);
        xmq_reply(['ok'=>true,'job'=>xmq_public($job)]);
    }
    if ($op === 'status') xmq_reply(['ok'=>true,'job'=>xmq_public($job)]);
    if ($op !== 'next') xmq_fail('Operação inválida.');
    if ($job['phase'] !== 'queued' || !empty($job['stop'])) xmq_reply(['ok'=>true,'job'=>xmq_public($job)]);
    $lease = xmq_active();
    if ($lease) xmq_reply(['ok'=>true,'job'=>xmq_public($job),'busy'=>true]);
    xmq_guard_health();
    $size = $job['mode'] === 'transfer' ? 2 : 25;
    $ids = array_slice($job['ids'], $job['cursor'], $size);
    $baseline = [];
    foreach (xmq_rows('SELECT stream_id,pid FROM streams_servers WHERE server_id=? AND stream_id IN (' . implode(',', array_map('intval', $ids)) . ')', $job['target']) as $row) $baseline[(string)$row['stream_id']] = intval($row['pid']);
    $token = bin2hex(random_bytes(32)); $hash = hash('sha256', $token);
    $job['batch'] = ['ids'=>$ids,'token_hash'=>$hash,'session'=>$identity['session'],'created'=>time(),'baseline'=>$baseline,'observed'=>[]];
    $job['phase'] = 'reserved';
    xmq_write(xmq_job_path($id), $job);
    xmq_write(XMQ_STATE . '/lease.json', ['job'=>$id]);
    xmq_write(XMQ_STATE . '/dispatch/' . $hash . '.json', ['job'=>$id]);
    $post = $job['post']; $post['streams'] = json_encode($ids);
    xmq_reply(['ok'=>true,'job'=>xmq_public($job),'dispatch'=>['token'=>$token,'post'=>$post]]);
} catch (Throwable $error) {
    error_log('XUI mass queue: ' . get_class($error));
    xmq_reply(['ok'=>false,'message'=>'A fila não conseguiu confirmar o estado. Nenhum lote será reenviado automaticamente.'], 503);
}
