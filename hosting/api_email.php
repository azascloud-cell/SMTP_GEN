<?php
/**
 * api_email.php — Proxy API untuk create/list/delete email via cPanel localhost
 *
 * Deploy file ini ke: public_html/api_email.php di hosting InfinityFree.
 * Bot memanggil endpoint ini via HTTP — tidak perlu akses langsung ke cPanel.
 *
 * Query params:
 *   key      — API key (harus sama dengan CPANEL_API_KEY di GitHub Secrets)
 *   action   — create | list | delete | ping
 *   email    — username email (tanpa @domain), wajib untuk create/delete
 *   password — password email, wajib untuk create
 *   quota    — quota MB (opsional, default 250)
 */

// ── Konfigurasi ──────────────────────────────────────────────────────────────
// Ganti nilai ini sebelum upload, atau biarkan auto-detect
define('CPANEL_USER', getenv('CPANEL_USERNAME') ?: _detect_cpanel_user());
define('CPANEL_PASS', getenv('CPANEL_PASSWORD') ?: '');   // isi manual jika perlu
define('API_KEY',     getenv('API_SECRET_KEY')  ?: '');   // harus diisi

// cPanel berjalan di localhost:2082 (non-SSL) di dalam server InfinityFree
define('CPANEL_HOST', 'localhost');
define('CPANEL_PORT', 2082);

header('Content-Type: application/json');
header('X-Powered-By: SMTP-Bot');

// ── Auth ──────────────────────────────────────────────────────────────────────
$key = $_GET['key'] ?? $_POST['key'] ?? '';
if (API_KEY === '' || $key !== API_KEY) {
    http_response_code(401);
    echo json_encode(['ok' => false, 'error' => 'Unauthorized']);
    exit;
}

$action = strtolower(trim($_GET['action'] ?? 'ping'));

// ── Ping ──────────────────────────────────────────────────────────────────────
if ($action === 'ping') {
    echo json_encode([
        'ok'     => true,
        'msg'    => 'API aktif',
        'domain' => $_SERVER['HTTP_HOST'],
        'user'   => CPANEL_USER ?: '(belum set)',
    ]);
    exit;
}

// ── Validasi user & pass tersedia ─────────────────────────────────────────────
if (!CPANEL_USER || !CPANEL_PASS) {
    echo json_encode(['ok' => false, 'error' => 'CPANEL_USER / CPANEL_PASS belum dikonfigurasi di server.']);
    exit;
}

$domain = $_SERVER['HTTP_HOST'];

// ── Helper: panggil cPanel UAPI via localhost ─────────────────────────────────
function cpanel_uapi(string $module, string $func, array $params = []): array {
    $qs  = http_build_query($params);
    $url = sprintf('http://%s:%d/execute/%s/%s?%s',
        CPANEL_HOST, CPANEL_PORT, $module, $func, $qs);

    $ctx = stream_context_create([
        'http' => [
            'method'  => 'GET',
            'header'  => 'Authorization: Basic ' . base64_encode(CPANEL_USER . ':' . CPANEL_PASS) . "\r\n",
            'timeout' => 15,
        ],
    ]);
    $raw = @file_get_contents($url, false, $ctx);
    if ($raw === false) {
        // Coba curl sebagai fallback
        if (function_exists('curl_init')) {
            $ch = curl_init($url);
            curl_setopt_array($ch, [
                CURLOPT_RETURNTRANSFER => true,
                CURLOPT_TIMEOUT        => 15,
                CURLOPT_USERPWD        => CPANEL_USER . ':' . CPANEL_PASS,
                CURLOPT_HTTPAUTH       => CURLAUTH_BASIC,
            ]);
            $raw = curl_exec($ch);
            curl_close($ch);
        }
    }
    if (!$raw) {
        return ['status' => 0, 'errors' => ['Tidak bisa connect ke cPanel localhost.']];
    }
    $data = json_decode($raw, true);
    return is_array($data) ? $data : ['status' => 0, 'errors' => ['Respons cPanel tidak valid.']];
}

// ── Actions ───────────────────────────────────────────────────────────────────
switch ($action) {

    case 'create':
        $email    = preg_replace('/[^a-z0-9._\-]/i', '', $_GET['email'] ?? '');
        $password = $_GET['password'] ?? '';
        $quota    = intval($_GET['quota'] ?? 250);

        if (!$email || !$password) {
            echo json_encode(['ok' => false, 'error' => 'Parameter email dan password wajib.']);
            exit;
        }

        $res = cpanel_uapi('Email', 'add_pop', [
            'email'    => $email,
            'password' => $password,
            'domain'   => $domain,
            'quota'    => $quota,
        ]);

        if (($res['status'] ?? 0) == 1) {
            echo json_encode([
                'ok'        => true,
                'email'     => $email . '@' . $domain,
                'username'  => $email,
                'password'  => $password,
                'domain'    => $domain,
                'smtp_host' => 'mail.' . $domain,
                'smtp_port' => 587,
                'imap_host' => 'mail.' . $domain,
                'imap_port' => 993,
            ]);
        } else {
            $errs = $res['errors'] ?? [$res['message'] ?? 'Unknown error'];
            echo json_encode(['ok' => false, 'error' => implode('; ', (array)$errs)]);
        }
        break;

    case 'list':
        $res = cpanel_uapi('Email', 'list_pops', ['domain' => $domain]);
        if (($res['status'] ?? 0) == 1) {
            $accounts = array_map(fn($a) => $a['email'] ?? '', $res['data'] ?? []);
            echo json_encode(['ok' => true, 'accounts' => $accounts, 'count' => count($accounts)]);
        } else {
            echo json_encode(['ok' => false, 'error' => 'Gagal list email.', 'accounts' => []]);
        }
        break;

    case 'delete':
        $email = preg_replace('/[^a-z0-9._\-]/i', '', $_GET['email'] ?? '');
        if (!$email) {
            echo json_encode(['ok' => false, 'error' => 'Parameter email wajib.']);
            exit;
        }
        $res = cpanel_uapi('Email', 'delete_pop', ['email' => $email, 'domain' => $domain]);
        echo json_encode([
            'ok'    => ($res['status'] ?? 0) == 1,
            'email' => $email . '@' . $domain,
            'error' => implode('; ', (array)($res['errors'] ?? [])),
        ]);
        break;

    default:
        http_response_code(400);
        echo json_encode(['ok' => false, 'error' => "Action tidak dikenal: $action"]);
}

// ── Detect cPanel username dari environment InfinityFree ──────────────────────
function _detect_cpanel_user(): string {
    // InfinityFree set env USER atau biasanya bisa dibaca dari path
    foreach (['USER', 'LOGNAME', 'USERNAME'] as $k) {
        $v = getenv($k);
        if ($v && (str_starts_with($v, 'if0_') || str_starts_with($v, 'epiz_'))) {
            return $v;
        }
    }
    // Coba baca dari direktori home
    $home = getenv('HOME') ?: '';
    if (preg_match('#/home/([^/]+)#', $home, $m)) return $m[1];
    return '';
}
