' launch_fuxi.vbs — 静默启动伏羲知识库（无控制台窗口）
' 双击 .lnk（指向本 vbs）时，隐藏窗口调用 fuxi_launch.ps1
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
ps1Path = scriptDir & "\fuxi_launch.ps1"

' 用 -ExecutionPolicy Bypass 绕过执行策略，-NoProfile -WindowStyle Hidden 静默运行
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & ps1Path & """", 0, False
