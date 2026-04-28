' run_silent.vbs — 執行 Symbiont 腳本不彈出 cmd 視窗
' 用法：wscript //B run_silent.vbs <script_name>
' 例：wscript //B "C:\path\run_silent.vbs" babysit

Dim oFS, oShell, agentDir, scriptName, cmd
Set oFS = CreateObject("Scripting.FileSystemObject")
Set oShell = CreateObject("WScript.Shell")

agentDir = oFS.GetParentFolderName(WScript.ScriptFullName)

If WScript.Arguments.Count > 0 Then
    scriptName = WScript.Arguments(0)
Else
    scriptName = "babysit"
End If

cmd = "cmd /c cd /d """ & agentDir & """ && python src\" & scriptName & ".py"
oShell.Run cmd, 0, False
