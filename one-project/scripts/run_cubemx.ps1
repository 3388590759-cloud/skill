# 无人值守运行 STM32CubeMX 重新生成一个 .ioc（只在用户要求验证"重新生成是否安全"时手动使用，不是自动门槛）。
# CubeMX 会被模态对话框卡住（如 "New STM32Cube firmware version available"），这里每 3 秒查一次可见窗口：
# 出现对话框就结束进程并报出标题，交给用户处理。只对 scratchpad 里的副本运行，不要对真实工程直接运行。
# 用法：powershell -NoProfile -File run_cubemx.ps1 -Ioc <ioc 绝对路径> [-TimeoutSec 300] [-MxPath <STM32CubeMX.exe>]
# 退出码：0 生成完成；3 出现对话框；4 超时；5 找不到 CubeMX
param([Parameter(Mandatory = $true)][string]$Ioc, [int]$TimeoutSec = 300, [string]$MxPath = $env:CUBEMX)

if (-not $MxPath) {
    foreach ($p in 'D:\Program Files\STMicroelectronics\STM32Cube\STM32CubeMX\STM32CubeMX.exe',
                   'C:\Program Files\STMicroelectronics\STM32Cube\STM32CubeMX\STM32CubeMX.exe') {
        if (Test-Path -LiteralPath $p) { $MxPath = $p; break }
    }
}
if (-not $MxPath -or -not (Test-Path -LiteralPath $MxPath)) { 'NO_CUBEMX'; exit 5 }

Add-Type @'
using System; using System.Text; using System.Runtime.InteropServices; using System.Collections.Generic;
public class OneProjectWin { public delegate bool P(IntPtr h, IntPtr l);
 [DllImport("user32.dll")] public static extern bool EnumWindows(P f, IntPtr l);
 [DllImport("user32.dll")] public static extern int GetWindowThreadProcessId(IntPtr h, out int pid);
 [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
 [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
 public static List<string> Visible(int target) { var r = new List<string>(); EnumWindows((h,l)=>{ int pid; GetWindowThreadProcessId(h, out pid);
   if (pid==target && IsWindowVisible(h)) { var sb=new StringBuilder(512); GetWindowText(h, sb, 512); if (sb.Length>0) r.Add(sb.ToString()); } return true; }, IntPtr.Zero); return r; } }
'@

$script = Join-Path $env:TEMP ('mx_' + [guid]::NewGuid().ToString('N') + '.txt')
[IO.File]::WriteAllText($script, "config load $Ioc`r`nproject generate`r`nexit`r`n", (New-Object Text.UTF8Encoding $false))
function Cleanup { Start-Sleep -Milliseconds 500; try { Remove-Item -LiteralPath $script -ErrorAction Stop } catch {} }

$before = @(Get-Process javaw -ErrorAction SilentlyContinue | ForEach-Object Id)
Start-Process -FilePath $MxPath -ArgumentList @('-q', "`"$script`"") | Out-Null
$t0 = Get-Date; $jp = $null
while (((Get-Date) - $t0).TotalSeconds -lt $TimeoutSec) {
    Start-Sleep -Seconds 3
    if (-not $jp) { $jp = Get-Process javaw -ErrorAction SilentlyContinue | Where-Object { $before -notcontains $_.Id } | Select-Object -First 1 }
    if (-not $jp) { continue }
    $jp.Refresh()
    if ($jp.HasExited) { Cleanup; "DONE secs=$([int]((Get-Date) - $t0).TotalSeconds)"; exit 0 }
    $v = [OneProjectWin]::Visible($jp.Id)
    if ($v.Count -gt 0) { Stop-Process -Id $jp.Id -Force; Cleanup; 'DIALOG: ' + ($v -join ' | '); exit 3 }
}
if ($jp) { Stop-Process -Id $jp.Id -Force }
Cleanup; 'TIMEOUT'; exit 4
