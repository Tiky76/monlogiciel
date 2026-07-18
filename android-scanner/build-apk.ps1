$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$gradleCommand = Get-Command gradle -ErrorAction SilentlyContinue

if (-not $gradleCommand) {
    Write-Host "Gradle est introuvable dans le terminal." -ForegroundColor Yellow
    Write-Host "Ouvrez android-scanner dans Android Studio, attendez la synchronisation, puis utilisez Build > Build APK." -ForegroundColor Yellow
    exit 1
}

Push-Location $projectDir
try {
    gradle assembleDebug
    $apkPath = Join-Path $projectDir "app\build\outputs\apk\debug\app-debug.apk"
    if (Test-Path $apkPath) {
        Write-Host "APK generee : $apkPath" -ForegroundColor Green
    } else {
        Write-Host "Compilation terminee, mais APK introuvable a l'emplacement attendu." -ForegroundColor Yellow
        exit 1
    }
}
finally {
    Pop-Location
}
