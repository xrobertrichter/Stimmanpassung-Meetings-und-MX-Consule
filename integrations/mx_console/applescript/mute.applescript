-- MX Creative Console action "mute": Ausgang in 30 ms stumm / wieder an (Dial drücken)
-- Runs the shell action of the same name; the shell script shows the notification.
-- Adjust repoRoot if the repository is not in your home folder under "voicechain".
set repoRoot to (POSIX path of (path to home folder)) & "voicechain"
set actionScript to repoRoot & "/integrations/mx_console/actions/mute.sh"
try
	do shell script quoted form of actionScript
on error errMsg
	display notification errMsg with title "voicechain"
end try
