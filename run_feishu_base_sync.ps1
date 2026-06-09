param(
    [int]$IntervalSeconds = 30,
    [switch]$Once
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Script = Join-Path $Root "feishu_base_sync.py"
$Db = Join-Path $Root "feishu_base_records.sqlite"
$Csv = Join-Path $Root "feishu_base_records.csv"

$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) {
    $Python = Get-Command py -ErrorAction SilentlyContinue
}
if (-not $Python) {
    throw "Python was not found on PATH."
}

$Args = @(
    $Script,
    "--db", $Db,
    "--csv", $Csv
)

if ($Once) {
    & $Python.Source @Args
} else {
    & $Python.Source @Args "--watch" "--interval" $IntervalSeconds
}

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
