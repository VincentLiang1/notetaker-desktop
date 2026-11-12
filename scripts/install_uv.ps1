# 確保這台有夠新的 uv(Windows)。**已經有 uv 就沿用、不替換**(使用者的 uv 可能是 winget/pip/公司派送裝的,
# 別的專案也在用),只檢查版本:比 `$UvMinVersion` 舊就停下(throw 以 `UV_TOO_OLD` 開頭,呼叫端換成「請先更新 uv」)。
# 沒有 uv 才裝固定版本:下載官方壓縮檔 → 比對 SHA-256 → 對得上才解開放進 %USERPROFILE%\.local\bin。
#
# 呼叫的有兩處:`Install.bat`(交付版第一次安裝)與 `packaging\core\install.ps1`(核心包)。兩邊都**讀檔成
# scriptblock 再執行**,不用 `-File`:群組原則把執行原則鎖住時 `-ExecutionPolicy Bypass` 擋不下來,而從網路
# 下載的 zip 解出來的 .ps1 還帶著網路標記;原本的 `irm … | iex` 也從來不受執行原則管。
# 成功就正常結束;失敗一律 throw(呼叫端接住、換成自己的訊息與離開碼)。
# ⚠️ **所以兩個呼叫端每次都叫它**(不先自己判斷有沒有 uv):版本檢查要在「已經有 uv」那條路上才做得到。
#
# ⚠️ 2026-11-12 使用者:「uv 也改成固定版本、比對檢查碼」(Mac 2026-11-11 先做了,`make_mac_app.UV_INSTALL_ZSH`)。
# ⚠️ **不用官方的 install.ps1**:那支就算鎖了版本也只是下載壓縮檔、**不比對任何檢查碼**(Mac 的 install.sh 有寫死
# 各平台的 sha256,Windows 那支沒有),鎖了安裝程式等於沒鎖 uv 本體。所以直接下載 GitHub Release 上的壓縮檔,
# 自己比對——雜湊抄自那一版 Release 附檔的 digest。⚠️ **升版本:版號與兩個雜湊一起換,而且要跟 Mac 那邊
# (`make_mac_app.UV_INSTALL_ZSH` 的 UV_VERSION)同一版**,測試釘著。
#
# 參數只給測試用(換來源、換雜湊、換目的地、不動登錄檔);兩個呼叫端一律不帶參數,測試也釘著。
# ⚠️ 這個檔是 UTF-8 加 BOM:PowerShell 5.1 沒有 BOM 會用系統碼頁讀,中文註解就壞了。
param(
    [string]$BaseUrl = '',
    [string]$Sha256 = '',
    [string]$Dest = '',
    [switch]$NoModifyPath
)
$ErrorActionPreference = 'Stop'
# PowerShell 5.1 的下載進度條會讓下載慢上好幾倍
$ProgressPreference = 'SilentlyContinue'

$UvVersion = '0.12.18'
#: 已經有 uv 時最低要多新(2026-11-12 使用者:「好,補上最低版本檢查」)。⚠️ 太舊的 uv 讀不懂我們的 `uv.lock`,
#: `uv sync --frozen` 失敗時只有一句英文,看不出是 uv 太舊。這個值的根據見 `docs/dev/publish.md` §4.5「uv 的最低版本」
$UvMinVersion = '0.6.0'
$UvSha256 = @{
    'x86_64'  = 'cae6a3bc25239f83dffb467a4b180508d9da23986c04639ebfa44e43e6a84bff'
    'aarch64' = '17f27b1c64eacc757ae603579f116a014881e486c5e79ae81877980d4699e943'
}

$arch = 'x86_64'
if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') { $arch = 'aarch64' }
$name = "uv-$arch-pc-windows-msvc.zip"
if (-not $BaseUrl) { $BaseUrl = "https://github.com/astral-sh/uv/releases/download/$UvVersion" }
if (-not $Sha256) { $Sha256 = $UvSha256[$arch] }
if (-not $Dest) { $Dest = Join-Path $env:USERPROFILE '.local\bin' }

# 已經有 uv:先找 PATH 上那一顆(之後 `uv sync` 用的就是它),再找我們會裝到的位置
$existing = @(Get-Command uv -CommandType Application -ErrorAction SilentlyContinue | ForEach-Object { $_.Source }) +
    @(Join-Path $Dest 'uv.exe') | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
if ($existing) {
    # ⚠️ 不用 `2>$null`:PowerShell 5.1 在 Stop 之下會把外部程式的 stderr 當成終止性例外
    try { $said = (& $existing --version | Out-String) } catch { $said = '' }
    # 認不出版號(輸出格式變了)就不擋:這道檢查是為了把錯誤講清楚,不是多一個裝不起來的理由
    if ($said -match 'uv (\d+\.\d+\.\d+)' -and [version]$Matches[1] -lt [version]$UvMinVersion) {
        throw "UV_TOO_OLD: uv $($Matches[1]) ($existing) is older than the minimum $UvMinVersion"
    }
    return
}

$tmp = Join-Path ([IO.Path]::GetTempPath()) ('uv-install-' + [guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    # 舊的 Windows 10 預設不開 TLS 1.2,GitHub 只收 1.2 以上
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $zip = Join-Path $tmp $name
    $wc = New-Object Net.WebClient
    # 公司電腦走系統的代理伺服器設定,並帶目前登入者的身分(需要驗證的代理)
    if ($wc.Proxy) { $wc.Proxy.Credentials = [Net.CredentialCache]::DefaultCredentials }
    $wc.DownloadFile("$BaseUrl/$name", $zip)

    # ⚠️ 先比對、對得上才解壓:解壓本身就是在處理一個還沒驗過的檔
    $got = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($got -ne $Sha256.ToLowerInvariant()) {
        throw "uv download checksum mismatch ($name): expected $Sha256, got $got"
    }
    $out = Join-Path $tmp 'x'
    Expand-Archive -LiteralPath $zip -DestinationPath $out
    if (-not (Test-Path -LiteralPath (Join-Path $out 'uv.exe'))) { throw "uv.exe is not in $name" }

    # 驗過、解開都成功了才動目的地:失敗時那裡什麼都不會多
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    foreach ($exe in 'uv.exe', 'uvx.exe', 'uvw.exe') {
        $src = Join-Path $out $exe
        if (Test-Path -LiteralPath $src) { Copy-Item -LiteralPath $src -Destination $Dest -Force }
    }

    if (-not $NoModifyPath) {
        # 加進使用者的 PATH:`Launch.vbs` 環境過期時走 `uv run`,靠的就是 PATH 找得到 uv。
        # ⚠️ 照官方安裝程式的寫法讀**未展開**的值、寫回 REG_EXPAND_SZ:用 [Environment]::SetEnvironmentVariable
        # 會把裡面的 %VAR% 永久展開成死路徑,型別也變成 REG_SZ
        $key = 'registry::HKEY_CURRENT_USER\Environment'
        $dirs = @((Get-Item -LiteralPath $key).GetValue('Path', '', 'DoNotExpandEnvironmentNames') -split ';' -ne '')
        if ($dirs -notcontains $Dest) {
            Set-ItemProperty -Type ExpandString -LiteralPath $key -Name Path -Value ((, $Dest + $dirs) -join ';')
            # 通知檔案總管重讀環境變數(同官方安裝程式:設一個再刪掉,會廣播 WM_SETTINGCHANGE)
            $dummy = 'uv-install-' + [guid]::NewGuid().ToString()
            [Environment]::SetEnvironmentVariable($dummy, '1', 'User')
            [Environment]::SetEnvironmentVariable($dummy, [NullString]::Value, 'User')
        }
    }
} finally {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
