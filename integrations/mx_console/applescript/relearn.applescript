-- MX Creative Console action "relearn": Regelung auf Kalibrierwerte, Lernphase neu, Analyzer leer; Offsets bleiben
-- Runs the shell action of the same name (it chooses the Python with the project's
-- dependencies: .venv, VOICECHAIN_PYTHON or Homebrew) and shows the answer as a
-- notification. Adjust repoRoot if the repository is not ~/voicechain.
set repoRoot to (POSIX path of (path to home folder)) & "voicechain"
set actionScript to repoRoot & "/integrations/mx_console/actions/relearn.sh"
try
	do shell script quoted form of actionScript
on error errMsg
	display notification errMsg with title "voicechain"
end try
