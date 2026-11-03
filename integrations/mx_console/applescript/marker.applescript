-- MX Creative Console action "marker": Zeitmarke mit Text ins Regelungslog
-- Runs the shell action of the same name; the shell script shows the notification.
-- Adjust repoRoot if the repository is not in your home folder under "voicechain".
set repoRoot to (POSIX path of (path to home folder)) & "voicechain"
set actionScript to repoRoot & "/integrations/mx_console/actions/marker.sh"
try
	do shell script quoted form of actionScript
on error errMsg
	display notification errMsg with title "voicechain"
end try
