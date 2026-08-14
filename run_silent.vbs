' 더블클릭하면 콘솔창 없이 검색기를 실행합니다.
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
shell.Run "pythonw main.py", 0, False
