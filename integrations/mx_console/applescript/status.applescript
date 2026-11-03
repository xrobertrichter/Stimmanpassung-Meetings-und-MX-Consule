-- MX Creative Console action "status": Mitteilung: Lautheit, Störabstand, Offsets, Freeze/Mute/Bypass/OFF
-- Runs the shell action of the same name (it chooses the Python with the project's
-- dependencies: .venv, VOICECHAIN_PYTHON or Homebrew) and shows the answer as a
-- notification. Adjust repoRoot if the repository is not ~/voicechain.
set repoRoot to (POSIX path of (path to home folder)) & "voicechain"
set actionScript to repoRoot & "/integrations/mx_console/actions/status.sh"
try
	do shell script quoted form of actionScript
on error errMsg
	display notification errMsg with title "voicechain"
end try
