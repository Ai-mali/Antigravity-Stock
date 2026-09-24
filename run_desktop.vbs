' AC Stock Tracker — silent launcher (no console window flash).
' Double-click this file instead of run_desktop.bat.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("Wscript.Shell")
sh.CurrentDirectory = fso.GetParentFolderName(WScript.ScriptFullName)

' Prefer pythonw (no console); fall back to python only if pythonw is missing.
rc = sh.Run("cmd /c where pythonw >nul 2>nul", 0, True)
If rc = 0 Then
    sh.Run "pythonw desktop_app.py", 0, False
Else
    sh.Run "python desktop_app.py", 0, False
End If
