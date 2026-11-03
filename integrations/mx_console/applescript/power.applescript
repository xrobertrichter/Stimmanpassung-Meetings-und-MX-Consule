-- MX Creative Console action "power": Kill-Switch: nur Input Trim + Limiter rechnen, Analyzer und Regelung pausieren
-- Runs the shell action of the same name (it chooses the Python with the project's
-- dependencies: .venv, VOICECHAIN_PYTHON or Homebrew) and shows the answer as a
-- notification. Adjust repoRoot if the repository is not ~/voicechain.
set repoRoot to (POSIX path of (path to home folder)) & "voicechain"
set actionScript to repoRoot & "/integrations/mx_console/actions/power.sh"
try
	do shell script quoted form of actionScript
on error errMsg
	display notification errMsg with title "voicechain"
end try
