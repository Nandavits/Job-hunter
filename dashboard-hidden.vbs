' Starts the Job Hunter dashboard in the background (no window). Used by the 5-minute check
' and by the Job Hunter desktop icon. Does nothing when the dashboard is already running.
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = here
sh.Run "cmd /c """ & here & "\dashboard.bat"" --boot", 0, False
