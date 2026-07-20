# Pairing and recovery

## Start and pair

1. Start `moddebugpilot-agent` in the logged-in console session.
2. Verify all paths and restrict the bind address or firewall as required.
3. Enter the TLS-key passphrase and select **Start secure listeners**.
4. Open the displayed HTTPS Controller URL. Compare the browser certificate's
   SHA-256 fingerprint with the native Agent value before accepting it.
5. Select **Open pairing window** in the Agent.
6. Submit the six-digit code and a recognizable controller name in the browser.
7. Refresh native pending requests, compare the name and controller ID, and
   approve only the expected request.

A code expires after ten minutes and one request consumes it. Rejection does not
authorize the browser session. Restart pairing with a new code if any displayed
identity is unexpected.

## Prepare and run

1. Enter a Thunderstore Profile Code and safe Profile ID.
2. Upload the locally built Debug DLL.
3. Import the profile. Review and edit only the listed config files.
4. Install the verified bundle on the Agent.
5. Choose an instance name and unused debugger port, then launch.
6. Use the instance list to download screenshots or task-kill one tracked
   process. Additional instances receive incremented names and ports.

Do not start normal Lethal Company while the Agent has an active instance. The
Agent refuses the first debug launch when it detects an untracked game process.

## Normal shutdown

Select **Stop and restore** in the native Agent. It stops every tracked process,
restores Doorstop files and normal saves, cancels instance watchers, and closes
both listeners. After it reports that restoration completed, normal Steam launch
requires no additional file operation.

## Interrupted Agent recovery

On the next listener start, the Agent recovers bootstrap and save journals before
accepting API traffic. If recovery reports an invalid journal, missing normal
backup, unexpected backup, or archive collision:

1. Do not launch the game and do not delete either save directory.
2. Stop the Agent and confirm no Lethal Company process remains.
3. Copy the normal save directory, its `.moddebugpilot-normal` sibling, the
   Agent data root, and the two isolation journals to separate backup media.
4. Inspect journal `session_id` and `original_existed` values plus directory
   timestamps. Do not guess which directory is canonical.
5. Repair only after establishing the original directory from the copied
   evidence, then restart the Agent and let recovery finish.

The Agent deliberately does not overwrite ambiguous data. A bootstrap restore
failure follows the same rule: preserve the game directory and
`bootstrap-backup`, compare them with the installed game's expected files, and
repair before another job.
